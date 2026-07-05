# -*- coding: utf-8 -*-
"""
AutoWork 主程序示例
这是一个示例文件，展示如何使用 autowork_with_table UI
"""

import sys
import os
import json
import re
import shutil
import subprocess
from PySide6.QtWidgets import (QApplication, QMainWindow, QMessageBox, QLabel,
    QListWidgetItem, QMenu, QColorDialog, QFontDialog, QInputDialog,
    QDialog, QVBoxLayout, QHBoxLayout, QKeySequenceEdit, QDialogButtonBox,
    QComboBox, QSpinBox)
from PySide6.QtCore import Slot, QProcess, Qt, QTimer
from PySide6.QtGui import QColor, QBrush, QShortcut, QKeySequence, QFont, QAction
from autowork_with_table import Ui_MainWindow


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        
        # 设置窗口标题
        self.setWindowTitle("AutoWork - 自动化工作工具")
        
        # 初始化UI
        self.init_ui()
        
        # 连接信号和槽
        self.connect_signals()
    
    # 默认路径配置（首次运行时自动写入 settings.json）
    DEFAULT_PATHS = {
        "exe_dir": r"C:\Users\shen_zhe\Desktop\snooker\bin64",
        "videos_dir": r"C:\Users\shen_zhe\Desktop\videos",
        "cipher_tool": r"C:\Users\shen_zhe\Desktop\videos\AESBase64CipherTool.exe",
    }
    # 默认快捷键配置
    DEFAULT_SHORTCUTS = {
        "shortcut_flush": "F5",
        "shortcut_start": "Space",
        "shortcut_open_dir": "Ctrl+O",
    }
    # 默认高亮颜色（橙色）
    DEFAULT_HIGHLIGHT_COLOR = [220, 80, 20]

    @staticmethod
    def _get_app_dir():
        """获取应用程序所在目录（兼容 PyInstaller 打包后的路径）"""
        if getattr(sys, 'frozen', False):
            # PyInstaller 打包后，sys.executable 指向 .exe 文件
            return os.path.dirname(sys.executable)
        return os.path.dirname(os.path.abspath(__file__))

    def _get_settings_path(self):
        """获取配置文件路径，与 main.py / .exe 同目录"""
        return os.path.join(self._get_app_dir(), "settings.json")

    def _reload_settings_cache(self):
        """从 settings.json 一次性加载到内存缓存"""
        path = self._get_settings_path()
        self._settings_cache = dict(self.DEFAULT_PATHS)  # 默认值作为基础
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    self._settings_cache.update(json.load(f))
            except Exception:
                pass

    def _load_settings(self):
        """返回缓存的配置（不再读磁盘，如需刷新请调用 _reload_settings_cache）"""
        if not hasattr(self, '_settings_cache'):
            self._reload_settings_cache()
        return self._settings_cache

    def _save_settings(self, data):
        """将配置写入 settings.json，同时更新内存缓存"""
        path = self._get_settings_path()
        try:
            self._load_settings()  # 确保缓存已初始化
            self._settings_cache.update(data)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(self._settings_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.ui.show_log.appendPlainText(f"[警告] 保存配置失败: {e}")

    def _load_paths(self):
        """从配置加载路径，并设置实例属性"""
        settings = self._load_settings()
        self.exe_dir = settings.get("exe_dir", self.DEFAULT_PATHS["exe_dir"])
        self.videos_dir = settings.get("videos_dir", self.DEFAULT_PATHS["videos_dir"])
        self.cipher_tool = settings.get("cipher_tool", self.DEFAULT_PATHS["cipher_tool"])
        # 确保首次运行时将默认路径写入 settings.json
        if not os.path.exists(self._get_settings_path()):
            self._save_settings(self.DEFAULT_PATHS)

    def _restore_exe_selection(self):
        """从配置文件恢复上次选择的程序"""
        settings = self._load_settings()
        saved_exe = settings.get("last_exe", "")
        if saved_exe:
            for i in range(self.ui.choose_exe.count()):
                if self.ui.choose_exe.itemText(i) == saved_exe:
                    self.ui.choose_exe.setCurrentIndex(i)
                    self.ui.show_log.appendPlainText(f"[配置] 已恢复上次程序: {saved_exe}")
                    return

    def init_ui(self):
        """初始化UI组件"""
        # 一次性加载 settings.json 到缓存（后续所有 _load_settings 都读缓存）
        self._reload_settings_cache()
        # 加载路径配置
        self._load_paths()
        
        # 设置默认日期为当前日期（使用 Python datetime 避免 QDate 年份异常）
        from datetime import date as py_date
        from PySide6.QtCore import QDate
        today = py_date.today()
        self.ui.date.blockSignals(True)
        self.ui.date.setDate(QDate(today.year, today.month, today.day))
        self.ui.date.blockSignals(False)
        
        # 初始化程序下拉框 - 扫描 snooker/bin64 目录下的 SnookerTracking*.exe
        self._load_exe_list()
        # 恢复上次选择的程序
        self._restore_exe_selection()
        
        # 初始化设备代码列表 - 扫描 videos 目录下的设备文件夹
        self._load_device_list()
        
        # 在日志区域显示欢迎信息
        self.ui.show_log.appendPlainText("欢迎使用 AutoWork 工具！")
        self.ui.show_log.appendPlainText(f"程序目录: {self.exe_dir}")
        self.ui.show_log.appendPlainText(f"视频目录: {self.videos_dir}")
        self.ui.show_log.appendPlainText("请选择程序并开始工作...")
        
        # 存储当前选中的视频和帧数
        self.current_video = None
        self.current_frame = None
        
        # 存储运行的程序进程
        self.running_process = None
        
        # 存储当前日志文件路径（用于右键菜单定位）
        self._current_log_path = None
        
        # 异步解码相关
        self._decode_process = None
        self._pending_exe_path = None
        self._pending_detect_json = None
        
        # 初始化状态栏、右键菜单、快捷键、菜单栏
        self._init_statusbar()
        self._init_context_menus()
        self._init_menubar()
        self._init_shortcuts()
        # 从 settings.json 加载并应用用户自定义设置（高亮颜色、字号、字体等）
        self._apply_highlight_color()
        self._apply_font_size()
        self._apply_font_family()
    
    def _load_exe_list(self):
        """加载 snooker/bin64 目录下的 SnookerTracking*.exe 到程序下拉框"""
        import glob
        
        exe_dir = self.exe_dir
        if not os.path.exists(exe_dir):
            self.ui.show_log.appendPlainText(f"[警告] 目录不存在: {exe_dir}")
            return
        
        # 查找所有匹配的 exe 文件
        pattern = os.path.join(exe_dir, "*SnookerTracking*.exe")
        exe_files = glob.glob(pattern)
        
        if not exe_files:
            self.ui.show_log.appendPlainText(f"[警告] 未找到 SnookerTracking*.exe 文件")
            return
        
        # 清空并添加文件列表
        self.ui.choose_exe.clear()
        for exe_path in sorted(exe_files):
            exe_name = os.path.basename(exe_path)
            self.ui.choose_exe.addItem(exe_name)
        
        self.ui.show_log.appendPlainText(f"[程序] 找到 {len(exe_files)} 个可执行文件")

    def _load_device_list(self):
        """加载 videos 目录下的设备代码文件夹到 id_list"""
        videos_dir = self.videos_dir
        if not os.path.exists(videos_dir):
            self.ui.show_log.appendPlainText(f"[警告] 目录不存在: {videos_dir}")
            return
        
        # 获取所有子目录（设备代码）
        device_codes = []
        for item in os.listdir(videos_dir):
            item_path = os.path.join(videos_dir, item)
            if os.path.isdir(item_path):
                device_codes.append(item)
        
        if not device_codes:
            self.ui.show_log.appendPlainText(f"[警告] videos 目录下没有找到设备文件夹")
            return
        
        # 清空并添加设备代码列表
        self.ui.id_list.clear()
        for code in sorted(device_codes):
            self.ui.id_list.addItem(code)
        
        self.ui.show_log.appendPlainText(f"[设备] 找到 {len(device_codes)} 个设备代码")

    def _get_selected_date_str(self):
        """获取日期选择器中的日期，格式如 2026-07-05"""
        qdate = self.ui.date.date()
        date_str = qdate.toString("yyyy-MM-dd")
        return date_str

    def _load_videos_for_device(self, device_code):
        """根据设备代码和选中日期加载日志文件到 loacl_video_list（第二列）"""
        import glob
        
        videos_dir = self.videos_dir
        device_dir = os.path.join(videos_dir, device_code)
        
        if not os.path.exists(device_dir):
            self.ui.show_log.appendPlainText(f"[警告] 设备目录不存在: {device_dir}")
            return
        
        # 获取选中日期，构建日期子目录路径
        date_str = self._get_selected_date_str()
        date_dir = os.path.join(device_dir, date_str)
        
        # 清空第二列
        self.ui.loacl_video_list.clear()
        
        if not os.path.exists(date_dir):
            self.ui.show_log.appendPlainText(f"[提示] {device_code} 下没有 {date_str} 的日志 (查找路径: {date_dir})")
            return
        
        # 查找日期目录下的 txt 和 log 文件
        log_files = glob.glob(os.path.join(date_dir, '*.txt'))
        log_files += glob.glob(os.path.join(date_dir, '*.log'))
        
        for log_path in sorted(log_files):
            # 只显示文件名，如 20260705_131009.log
            self.ui.loacl_video_list.addItem(os.path.basename(log_path))
        
        self.ui.show_log.appendPlainText(f"[日志目录] {device_code}/{date_str} 下有 {len(log_files)} 个日志文件")

    def _load_logs_for_device(self, device_code):
        """初始化第三列为空，等待点击日志后展示内容"""
        # 第三列初始化为空，点击第二列的日志项后才填充内容
        self.ui.log_list.clear()

    def connect_signals(self):
        """连接信号和槽"""
        # 按钮点击事件
        self.ui.flush.clicked.connect(self.on_flush_clicked)
        self.ui.start.clicked.connect(self.on_start_clicked)
        self.ui.end.clicked.connect(self.on_end_clicked)
        self.ui.open_daily.clicked.connect(self.on_open_daily_clicked)
        self.ui.write_table.clicked.connect(self.on_open_dir_clicked)
        self.ui.open_config.clicked.connect(lambda: QTimer.singleShot(0, self.on_open_config_clicked))
        # 列表项选择事件
        self.ui.id_list.itemClicked.connect(self.on_id_selected)
        self.ui.loacl_video_list.itemClicked.connect(self.on_video_selected)
        self.ui.log_list.itemClicked.connect(self.on_log_selected)
        self.ui.log_list.itemDoubleClicked.connect(self.on_log_double_clicked)
        
        # 日期改变时重新加载第二列
        self.ui.date.dateChanged.connect(self._on_date_changed)
        
        # 程序下拉框切换时自动保存选择
        self.ui.choose_exe.currentTextChanged.connect(self._on_exe_changed)
        
        # 右键菜单信号
        self.ui.id_list.customContextMenuRequested.connect(self._id_list_context_menu)
        self.ui.log_list.customContextMenuRequested.connect(self._log_list_context_menu)
    
    @Slot()
    def on_flush_clicked(self):
        """刷新按钮点击事件"""
        self.ui.show_log.appendPlainText("\n[操作] 刷新数据...")
        
        # 先记住当前选中的设备代码和程序
        current_device = self.ui.id_list.currentItem()
        saved_device_code = current_device.text() if current_device else None
        saved_exe = self.ui.choose_exe.currentText()
        
        # 1. 重新扫描可执行程序下拉框
        self._load_exe_list()
        # 恢复程序选择
        for i in range(self.ui.choose_exe.count()):
            if self.ui.choose_exe.itemText(i) == saved_exe:
                self.ui.choose_exe.setCurrentIndex(i)
                break
        
        # 2. 重新扫描设备列表（第一列）
        self._load_device_list()
        
        # 3. 恢复之前选中的设备，并重新加载其日志目录（第二列）
        if saved_device_code:
            # 在刷新后的列表中找回该设备
            for i in range(self.ui.id_list.count()):
                if self.ui.id_list.item(i).text() == saved_device_code:
                    self.ui.id_list.setCurrentItem(self.ui.id_list.item(i))
                    self._load_videos_for_device(saved_device_code)
                    break
            self.ui.log_list.clear()
        
        self.ui.show_log.appendPlainText("[刷新] 完成")
        
    @Slot()
    def on_start_clicked(self):
        """播放按钮点击事件 - 启动 SnookerTracking 程序"""
        # 如果已经有程序在运行，先结束它
        if self.running_process is not None:
            self.ui.show_log.appendPlainText("\n[警告] 已有程序正在运行，请先点击'结束'")
            return
        
        # 获取选中的程序
        exe_name = self.ui.choose_exe.currentText()
        if not exe_name:
            QMessageBox.warning(self, "警告", "请先选择程序！")
            return
        
        exe_dir = self.exe_dir
        exe_path = os.path.join(exe_dir, exe_name)
        if not os.path.exists(exe_path):
            QMessageBox.warning(self, "警告", f"程序不存在: {exe_path}")
            return
        
        # 使用 QProcess 启动程序
        self.running_process = QProcess()
        self.running_process.setWorkingDirectory(exe_dir)
        
        # 连接信号以捕获输出
        self.running_process.readyReadStandardOutput.connect(self._on_program_output)
        self.running_process.readyReadStandardError.connect(self._on_program_error)
        self.running_process.finished.connect(self._on_program_finished)
        
        # 启动前准备 detect.json（可能需要异步解码）
        self._pending_exe_path = exe_path
        need_decode = self._prepare_detect_json()
        
        if need_decode:
            # 解码进行中，启动将在 _on_decode_finished 中继续
            self.ui.show_log.appendPlainText(f"\n[播放] 等待 detect.json 解码完成后启动...")
            self._update_status_running(exe_name)
        else:
            # 无需解码，直接启动
            self._launch_program(exe_path, exe_name, exe_dir)
    
    def _on_program_output(self):
        """处理程序的标准输出"""
        if self.running_process:
            output = self.running_process.readAllStandardOutput().data().decode('gb2312', errors='ignore')
            if output.strip():
                self.ui.show_log.appendPlainText(output.strip())
    
    def _on_program_error(self):
        """处理程序的错误输出"""
        if self.running_process:
            error = self.running_process.readAllStandardError().data().decode('gb2312', errors='ignore')
            if error.strip():
                self.ui.show_log.appendPlainText(f"[程序错误] {error.strip()}")
    
    def _on_program_finished(self, exit_code, exit_status):
        """程序结束时回调"""
        self.ui.show_log.appendPlainText(f"\n[程序结束] 退出码: {exit_code}")
        self.running_process = None
        self._update_status_idle()
        
    @Slot()
    def on_end_clicked(self):
        """结束按钮点击事件 - 停止运行的程序"""
        if self.running_process is None:
            self.ui.show_log.appendPlainText("\n[提示] 没有正在运行的程序")
            return
        
        # 直接强制终止进程
        self.ui.show_log.appendPlainText("\n[结束] 正在终止程序...")
        self.running_process.kill()  # 强制终止
        self.ui.show_log.appendPlainText("[结束] 程序已强制终止")
        self.running_process = None
        self._update_status_idle()
        
    @Slot()
    def on_open_daily_clicked(self):
        """打开 CPP 日志文件"""
        # 检查是否选中了设备
        if not self.ui.id_list.currentItem():
            self.ui.show_log.appendPlainText("[提示] 请先选择设备代码")
            return
        
        device_code = self.ui.id_list.currentItem().text()
        date_str = self._get_selected_date_str()
        daily_path = os.path.join(
            self.videos_dir, device_code, f"daily_{date_str}.txt"
        )
        
        if not os.path.exists(daily_path):
            self.ui.show_log.appendPlainText(f"[提示] CPP 日志文件不存在: {daily_path}")
            return
        
        # 用系统默认程序打开文件
        os.startfile(daily_path)
        self.ui.show_log.appendPlainText(f"[CPP日志] 已打开: {daily_path}")
        
    @Slot()
    def on_open_dir_clicked(self):
        """打开目录按钮点击事件 - 打开当前选中设备的目录"""
        if not self.ui.id_list.currentItem():
            self.ui.show_log.appendPlainText("[提示] 请先选择设备代码")
            return
        
        device_code = self.ui.id_list.currentItem().text()
        device_dir = os.path.join(self.videos_dir, device_code)
        
        if not os.path.exists(device_dir):
            self.ui.show_log.appendPlainText(f"[提示] 目录不存在: {device_dir}")
            return
        
        os.startfile(device_dir)
        self.ui.show_log.appendPlainText(f"[打开目录] {device_dir}")
    
    @Slot()
    def on_open_config_clicked(self):
        """配置按钮点击事件 - 选择打开 settings.json 或 cfg.json"""
        msg = QMessageBox(self)
        msg.setWindowTitle("打开配置文件")
        msg.setText("选择要打开的配置文件：")
        settings_btn = msg.addButton("settings.json", QMessageBox.ActionRole)
        cfg_btn = msg.addButton("cfg.json", QMessageBox.ActionRole)
        msg.addButton(QMessageBox.Cancel)
        msg.exec()
        
        clicked = msg.clickedButton()
        if clicked == settings_btn:
            path = self._get_settings_path()
        elif clicked == cfg_btn:
            path = os.path.join(self.exe_dir, "cfg.json")
        else:
            return
        
        if not os.path.exists(path):
            self.ui.show_log.appendPlainText(f"[配置] 文件不存在: {path}")
            return
        
        os.startfile(path)
        self.ui.show_log.appendPlainText(f"[配置] 已打开: {path}")
        
    @Slot()
    def on_id_selected(self, item):
        """ID列表项选中事件 - 加载对应设备的日志目录"""
        device_code = item.text()
        self.ui.show_log.appendPlainText(f"\n[设备选中] {device_code}")
        self._update_status_device(device_code)
        
        # 加载该设备下的日志目录到第二列
        self._load_videos_for_device(device_code)
        # 清空第三列
        self._load_logs_for_device(device_code)
        
    @Slot()
    def on_video_selected(self, item):
        """日志目录项选中事件 - 在第三列展示日志内容"""
        log_filename = item.text()
        self.ui.show_log.appendPlainText(f"\n[日志选中] {log_filename}")
        
        # 获取当前选中的设备代码
        if not self.ui.id_list.currentItem():
            return
        device_code = self.ui.id_list.currentItem().text()
        
        # 拼接完整路径：设备目录/日期/文件名
        date_str = self._get_selected_date_str()
        full_log_path = os.path.join(self.videos_dir, device_code, date_str, log_filename)
        
        # 读取日志文件内容并显示在第三列
        try:
            with open(full_log_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            self._current_log_path = full_log_path
            self.ui.log_list.clear()
            highlight_patterns = [r'返回', r'add']
            for line in content.splitlines():
                item = QListWidgetItem(line)
                if any(re.search(p, line) for p in highlight_patterns):
                    item.setForeground(QBrush(self.highlight_color))
                self.ui.log_list.addItem(item)
            
            line_count = len(content.splitlines())
            self.ui.show_log.appendPlainText(f"[日志内容] 已加载 {line_count} 行")
            self._update_status_logs(line_count)
        except Exception as e:
            self.ui.show_log.appendPlainText(f"[错误] 无法读取日志文件: {str(e)}")
            self.ui.log_list.clear()
            self._current_log_path = None
        
    def _get_frame_input_value(self):
        """获取输入框中的帧数值，默认 400"""
        try:
            return int(self.ui.input_frame.text().strip())
        except (ValueError, AttributeError):
            return 400

    def _compute_video_start_frame(self, log_frame_id):
        """根据单选按钮模式计算 video_start_frame
        - 帧前: log_frame_id - 输入值
        - 帧数: log_frame_id
        - 自定义: 输入值
        """
        if self.ui.input_frame_before.isChecked():
            offset = self._get_frame_input_value()
            result = log_frame_id - offset
            self.ui.show_log.appendPlainText(f"  [模式] 帧前: {log_frame_id} - {offset} = {result}")
            return result
        elif self.ui.input_frame_set.isChecked():
            self.ui.show_log.appendPlainText(f"  [模式] 帧数: {log_frame_id}")
            return log_frame_id
        elif self.ui.input_frame_custom.isChecked():
            custom = self._get_frame_input_value()
            self.ui.show_log.appendPlainText(f"  [模式] 自定义: {custom}")
            return custom
        else:
            offset = self._get_frame_input_value()
            return log_frame_id - offset

    def _launch_program(self, exe_path, exe_name, exe_dir):
        """实际启动主程序（在 detect.json 准备好之后调用）"""
        # 刷新 cfg.json（应用当前单选按钮模式）
        if self.current_video and self.current_frame is not None:
            video_start_frame = self._compute_video_start_frame(self.current_frame)
            self._update_cfg_json(self.current_video, video_start_frame)
        
        # 启动程序
        self.running_process.start(exe_path)
        self.ui.show_log.appendPlainText(f"\n[播放] 已启动程序: {exe_name}")
        self.ui.show_log.appendPlainText(f"  - 工作目录: {exe_dir}")
        self._update_status_running(exe_name)

    def _on_decode_output(self):
        """处理解码程序的标准输出"""
        if self._decode_process:
            output = self._decode_process.readAllStandardOutput().data().decode('gb2312', errors='ignore')
            if output.strip():
                self.ui.show_log.appendPlainText(f"[detect] {output.strip()}")

    def _on_decode_error(self):
        """处理解码程序的错误输出"""
        if self._decode_process:
            error = self._decode_process.readAllStandardError().data().decode('gb2312', errors='ignore')
            if error.strip():
                self.ui.show_log.appendPlainText(f"[detect] {error.strip()}")

    def _on_decode_finished(self, exit_code, exit_status):
        """解码完成后回调：复制 detect.json 并启动主程序"""
        self._decode_process = None
        
        detect_json_path = self._pending_detect_json
        exe_path = self._pending_exe_path
        exe_name = os.path.basename(exe_path)
        exe_dir = os.path.dirname(exe_path)
        
        if exit_code != 0:
            self.ui.show_log.appendPlainText(f"[detect] 解码失败，退出码: {exit_code}")
            self._pending_exe_path = None
            self._pending_detect_json = None
            self._update_status_idle()
            return
        
        # 验证解码结果
        if not os.path.exists(detect_json_path):
            self.ui.show_log.appendPlainText(f"[detect] 警告: 解码后未生成 detect.json")
            self._pending_exe_path = None
            self._pending_detect_json = None
            self._update_status_idle()
            return
        
        # 复制 detect.json 到程序目录
        target_path = os.path.join(self.exe_dir, "detect.json")
        try:
            shutil.copy2(detect_json_path, target_path)
            self.ui.show_log.appendPlainText(f"[detect] 已更新 detect.json -> {target_path}")
        except Exception as e:
            self.ui.show_log.appendPlainText(f"[detect] 复制失败: {e}")
        
        self._pending_exe_path = None
        self._pending_detect_json = None
        
        # 继续启动主程序
        self._launch_program(exe_path, exe_name, exe_dir)

    def _prepare_detect_json(self):
        """准备 detect.json：解密并复制到程序目录。
        返回 True 表示正在异步解码，返回 False 表示已同步完成或跳过。"""
        # 检查是否选中了设备
        if not self.ui.id_list.currentItem():
            self.ui.show_log.appendPlainText("[detect] 未选中设备，跳过 detect.json 处理")
            return False
        
        device_code = self.ui.id_list.currentItem().text()
        device_dir = os.path.join(self.videos_dir, device_code)
        detect_json_path = os.path.join(device_dir, "detect.json")
        detect_bin_path = os.path.join(device_dir, "detect.bin")
        
        json_exists = os.path.exists(detect_json_path)
        bin_exists = os.path.exists(detect_bin_path)
        
        # 判断是否需要解码
        need_decode = False
        if not json_exists:
            if not bin_exists:
                self.ui.show_log.appendPlainText(f"[detect] 警告: {device_code} 下既没有 detect.json 也没有 detect.bin")
                return False
            self.ui.show_log.appendPlainText("[detect] detect.json 不存在，将从 detect.bin 解码")
            need_decode = True
        elif bin_exists:
            # 两者都存在，比较修改时间
            bin_mtime = os.path.getmtime(detect_bin_path)
            json_mtime = os.path.getmtime(detect_json_path)
            if bin_mtime > json_mtime:
                self.ui.show_log.appendPlainText("[detect] detect.bin 比 detect.json 更新，重新解码")
                need_decode = True
            else:
                self.ui.show_log.appendPlainText("[detect] detect.json 已是最新，无需重新解码")
        
        if need_decode:
            # 使用 QProcess 异步调用 AESBase64CipherTool.exe 解码
            cipher_tool = self.cipher_tool
            if not os.path.exists(cipher_tool):
                self.ui.show_log.appendPlainText(f"[detect] 警告: 解码工具不存在: {cipher_tool}")
                return False
            
            self._pending_detect_json = detect_json_path
            cmd = [cipher_tool, detect_bin_path, detect_json_path]
            self.ui.show_log.appendPlainText(f"[detect] 正在异步解码: {' '.join(cmd)}")
            
            self._decode_process = QProcess()
            self._decode_process.readyReadStandardOutput.connect(self._on_decode_output)
            self._decode_process.readyReadStandardError.connect(self._on_decode_error)
            self._decode_process.finished.connect(self._on_decode_finished)
            self._decode_process.start(cmd[0], cmd[1:])
            return True
        
        # 不需要解码，直接同步复制 detect.json 到程序目录
        target_path = os.path.join(self.exe_dir, "detect.json")
        try:
            shutil.copy2(detect_json_path, target_path)
            self.ui.show_log.appendPlainText(f"[detect] 已更新 detect.json -> {target_path}")
        except Exception as e:
            self.ui.show_log.appendPlainText(f"[detect] 复制失败: {e}")
        return False

    def _update_cfg_json(self, video_path, frame):
        """更新 cfg.json 配置文件"""
        cfg_path = os.path.join(self.exe_dir, "cfg.json")
        if not os.path.exists(cfg_path):
            self.ui.show_log.appendPlainText(f"[警告] cfg.json 不存在: {cfg_path}")
            return False
        try:
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            if 'cap' in cfg and 'file' in cfg['cap']:
                cfg['cap']['file']['path'] = video_path
                cfg['cap']['file']['video_start_frame'] = frame
            if 'path' in cfg:
                del cfg['path']
            if 'video_start_frame' in cfg:
                del cfg['video_start_frame']
            with open(cfg_path, 'w', encoding='utf-8') as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            self.ui.show_log.appendPlainText(f"[配置] 已更新 cfg.json")
            self.ui.show_log.appendPlainText(f"  - 视频: {video_path}")
            self.ui.show_log.appendPlainText(f"  - 帧数: {frame}")
            return True
        except Exception as e:
            self.ui.show_log.appendPlainText(f"[错误] 更新 cfg.json 失败: {str(e)}")
            import traceback
            self.ui.show_log.appendPlainText(traceback.format_exc())
            return False

    @Slot()
    def on_log_selected(self, item):
        """日志列表项选中事件 - 解析日志并更新cfg.json"""
        log_line = item.text()
        self.ui.show_log.appendPlainText(f"\n[日志选中] {log_line}")
        
        # 解析日志：提取帧数
        frame_match = re.search(r'frame_id:(\d+)', log_line)
        if not frame_match:
            self.ui.show_log.appendPlainText("[警告] 日志中未找到 frame_id")
            return
        
        log_frame_id = int(frame_match.group(1))
        self.current_frame = log_frame_id
        
        # 获取当前选中的设备代码
        if not self.ui.id_list.currentItem():
            self.ui.show_log.appendPlainText("[警告] 未选择设备代码")
            return
        
        # 从第二列获取当前选中的日志文件名，推断视频文件名
        if not self.ui.loacl_video_list.currentItem():
            self.ui.show_log.appendPlainText("[警告] 未选择日志文件")
            return
        
        log_filename = self.ui.loacl_video_list.currentItem().text()
        video_name = os.path.splitext(log_filename)[0] + '.mp4'
        video_path = os.path.join(self.videos_dir, "videos", video_name).replace(os.sep, '/')
        self.current_video = video_path
        
        # 根据单选按钮模式计算实际起始帧
        video_start_frame = self._compute_video_start_frame(log_frame_id)
        
        # 更新 cfg.json
        self._update_cfg_json(video_path, video_start_frame)

    def _on_date_changed(self, date):
        """日期改变时重新加载第二列日志列表"""
        current_device = self.ui.id_list.currentItem()
        if current_device:
            device_code = current_device.text()
            self._load_videos_for_device(device_code)
            self.ui.log_list.clear()

    def _on_exe_changed(self, exe_name):
        """程序下拉框改变时保存选择到配置文件"""
        if exe_name:
            self._save_settings({"last_exe": exe_name})
            self.ui.show_log.appendPlainText(f"[配置] 已保存程序选择: {exe_name}")

    @Slot()
    def on_log_double_clicked(self, item):
        """日志列表项双击事件 - 解析日志、更新cfg.json并启动程序"""
        # 先触发选中逻辑（更新cfg.json）
        self.on_log_selected(item)
        
        # 然后启动播放
        self.on_start_clicked()

    # ==================== 状态栏 ====================

    def _init_statusbar(self):
        """初始化底部状态栏，添加永久性标签"""
        self.status_device = QLabel("设备: 未选择")
        self.status_state = QLabel("状态: 空闲")
        self.status_logs = QLabel("日志: 0 行")
        sb = self.statusBar()
        sb.addPermanentWidget(self.status_device)
        sb.addPermanentWidget(QLabel(" | "))
        sb.addPermanentWidget(self.status_state)
        sb.addPermanentWidget(QLabel(" | "))
        sb.addPermanentWidget(self.status_logs)
        sb.showMessage("就绪", 3000)

    def _update_status_device(self, device_code):
        self.status_device.setText(f"设备: {device_code}")

    def _update_status_running(self, exe_name):
        self.status_state.setText(f"状态: 运行中 - {exe_name}")

    def _update_status_idle(self):
        self.status_state.setText("状态: 空闲")

    def _update_status_logs(self, count):
        self.status_logs.setText(f"日志: {count} 行")

    # ==================== 右键菜单 ====================

    def _init_context_menus(self):
        """为列表控件设置自定义右键菜单策略"""
        self.ui.id_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.ui.log_list.setContextMenuPolicy(Qt.CustomContextMenu)

    def _id_list_context_menu(self, pos):
        """设备列表右键菜单"""
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        action_open_dir = menu.addAction("打开目录")
        action_cpp_log = menu.addAction("查看 CPP 日志")
        action = menu.exec(self.ui.id_list.mapToGlobal(pos))
        if action == action_open_dir:
            self.on_open_dir_clicked()
        elif action == action_cpp_log:
            self.on_open_daily_clicked()

    def _log_list_context_menu(self, pos):
        """日志内容列表右键菜单"""
        from PySide6.QtWidgets import QMenu
        menu = QMenu(self)
        action_copy = menu.addAction("复制此行")
        action_locate = menu.addAction("在文件管理器中定位")
        action = menu.exec(self.ui.log_list.mapToGlobal(pos))
        if action == action_copy:
            item = self.ui.log_list.currentItem()
            if item:
                QApplication.clipboard().setText(item.text())
                self.statusBar().showMessage("已复制到剪贴板", 2000)
        elif action == action_locate:
            if self._current_log_path and os.path.exists(self._current_log_path):
                subprocess.run(['explorer', '/select,', self._current_log_path])
            else:
                self.ui.show_log.appendPlainText("[提示] 无法定位日志文件")

    # ==================== 快捷键 ====================

    def _get_shortcut_settings(self):
        """从 settings.json 获取快捷键配置，缺失字段用默认值"""
        settings = self._load_settings()
        return {
            "shortcut_flush": settings.get("shortcut_flush", self.DEFAULT_SHORTCUTS["shortcut_flush"]),
            "shortcut_start": settings.get("shortcut_start", self.DEFAULT_SHORTCUTS["shortcut_start"]),
            "shortcut_open_dir": settings.get("shortcut_open_dir", self.DEFAULT_SHORTCUTS["shortcut_open_dir"]),
        }

    def _init_shortcuts(self):
        """绑定全局快捷键（从 settings.json 读取配置）"""
        # 清除旧快捷键引用
        self._shortcuts = []
        sc = self._get_shortcut_settings()
        # 刷新
        s1 = QShortcut(QKeySequence(sc["shortcut_flush"]), self)
        s1.activated.connect(self.on_flush_clicked)
        self._shortcuts.append(s1)
        # 打开目录
        s2 = QShortcut(QKeySequence(sc["shortcut_open_dir"]), self)
        s2.activated.connect(self.on_open_dir_clicked)
        self._shortcuts.append(s2)
        # 空格切换播放/结束
        self._space_shortcut = QShortcut(QKeySequence(sc["shortcut_start"]), self)
        self._space_shortcut.activated.connect(self._on_space_pressed)
        self._shortcuts.append(self._space_shortcut)

    def _on_space_pressed(self):
        """空格键切换播放/结束，焦点在输入框时不触发"""
        if self.focusWidget() is self.ui.input_frame:
            return
        if self.running_process is not None:
            self.on_end_clicked()
        else:
            self.on_start_clicked()

    # ==================== 菜单栏 ====================

    def _init_menubar(self):
        """初始化顶部菜单栏"""
        menubar = self.menuBar()

        # 「功能」菜单
        func_menu = menubar.addMenu("功能")
        act_sc = func_menu.addAction("修改快捷键")
        act_sc.triggered.connect(lambda: QTimer.singleShot(0, self._on_modify_shortcuts))
        act_hc = func_menu.addAction("高亮颜色设置")
        act_hc.triggered.connect(lambda: QTimer.singleShot(0, self._on_highlight_color))

        # 「主题」菜单
        theme_menu = menubar.addMenu("主题")
        act_fs = theme_menu.addAction("字号大小")
        act_fs.triggered.connect(lambda: QTimer.singleShot(0, self._on_font_size))
        act_scale = theme_menu.addAction("界面缩放")
        act_scale.triggered.connect(lambda: QTimer.singleShot(0, self._on_dpi_scale))
        act_ff = theme_menu.addAction("字体设置")
        act_ff.triggered.connect(lambda: QTimer.singleShot(0, self._on_font_family))

        # 「帮助」菜单
        help_menu = menubar.addMenu("帮助")
        act_about = help_menu.addAction("关于")
        act_about.triggered.connect(lambda: QTimer.singleShot(0, self._on_about))

    def _on_modify_shortcuts(self):
        """弹出快捷键修改对话框"""
        dlg = QDialog(self)
        dlg.setWindowTitle("修改快捷键")
        layout = QVBoxLayout(dlg)
        sc = self._get_shortcut_settings()
        fields = [
            ("刷新", "shortcut_flush", sc["shortcut_flush"]),
            ("播放/结束", "shortcut_start", sc["shortcut_start"]),
            ("打开目录", "shortcut_open_dir", sc["shortcut_open_dir"]),
        ]
        editors = {}
        for label, key, default in fields:
            row = QHBoxLayout()
            row.addWidget(QLabel(label + ":"))
            edit = QKeySequenceEdit(QKeySequence(default))
            row.addWidget(edit)
            editors[key] = edit
            layout.addLayout(row)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)
        if dlg.exec() == QDialog.Accepted:
            new_sc = {k: v.keySequence().toString() for k, v in editors.items()}
            self._save_settings(new_sc)
            self._init_shortcuts()
            self.ui.show_log.appendPlainText(f"[配置] 已更新快捷键: {new_sc}")

    def _on_highlight_color(self):
        """弹出颜色选择对话框修改日志高亮颜色"""
        current = self.highlight_color
        color = QColorDialog.getColor(current, self, "选择高亮颜色")
        if color.isValid():
            self.highlight_color = color
            self._save_settings({
                "highlight_color": [color.red(), color.green(), color.blue()]
            })
            self.ui.show_log.appendPlainText(
                f"[配置] 已更新高亮颜色: RGB({color.red()},{color.green()},{color.blue()})")

    def _on_font_size(self):
        """弹出字号选择对话框"""
        settings = self._load_settings()
        current = settings.get("font_size", 10)
        val, ok = QInputDialog.getInt(self, "字号大小", "请输入字号 (10~20):", current, 10, 20, 1)
        if ok:
            self._save_settings({"font_size": val})
            self._apply_font_size()
            self.ui.show_log.appendPlainText(f"[配置] 已更新字号: {val}pt")

    def _on_dpi_scale(self):
        """弹出 DPI 缩放比例选择对话框"""
        settings = self._load_settings()
        current = settings.get("dpi_scale", 100)
        options = [100, 125, 150, 175, 200]
        idx, ok = QInputDialog.getItem(
            self, "界面缩放", "选择缩放比例:",
            [f"{o}%" for o in options],
            options.index(current) if current in options else 0,
            editable=False)
        if ok:
            val = int(idx.replace("%", ""))
            self._save_settings({"dpi_scale": val})
            QMessageBox.information(self, "界面缩放", "缩放设置已保存，重启应用后生效。")
            self.ui.show_log.appendPlainText(f"[配置] 已设置缩放: {val}%（重启后生效）")

    def _on_font_family(self):
        """弹出字体选择对话框"""
        settings = self._load_settings()
        current_family = settings.get("font_family", "")
        current_font = QFont(current_family) if current_family else QFont()
        font, ok = QFontDialog.getFont(current_font, self, "选择字体")
        if ok:
            self._save_settings({"font_family": font.family()})
            self._apply_font_family()
            self.ui.show_log.appendPlainText(f"[配置] 已更新字体: {font.family()}")

    def _on_about(self):
        """显示关于对话框"""
        QMessageBox.about(
            self,
            "关于",
            "AutoWork - 自动化工作工具\n"
            "版本: 1.1\n\n"
            "用于视频播放、日志管理与数据记录的桌面自动化工具。"
        )

    # ==================== 设置应用 ====================

    def _apply_highlight_color(self):
        """从 settings.json 加载高亮颜色"""
        settings = self._load_settings()
        rgb = settings.get("highlight_color", self.DEFAULT_HIGHLIGHT_COLOR)
        self.highlight_color = QColor(rgb[0], rgb[1], rgb[2])

    def _apply_font_size(self):
        """从 settings.json 加载并应用全局字号"""
        settings = self._load_settings()
        size = settings.get("font_size", None)
        if size:
            font = QApplication.font()
            font.setPointSize(size)
            QApplication.setFont(font)

    def _apply_font_family(self):
        """从 settings.json 加载并应用全局字体"""
        settings = self._load_settings()
        family = settings.get("font_family", None)
        if family:
            font = QApplication.font()
            font.setFamily(family)
            QApplication.setFont(font)

    @staticmethod
    def apply_dpi_scale(settings_path):
        """在 QApplication 创建后应用 DPI 缩放"""
        try:
            with open(settings_path, 'r', encoding='utf-8') as f:
                settings = json.load(f)
            scale = settings.get("dpi_scale", 100)
            if scale != 100:
                os.environ["QT_SCALE_FACTOR"] = str(scale / 100.0)
        except Exception:
            pass


def main():
    """主函数"""
    # 应用 DPI 缩放（必须在 QApplication 创建前设置环境变量）
    settings_path = os.path.join(MainWindow._get_app_dir(), "settings.json")
    MainWindow.apply_dpi_scale(settings_path)

    app = QApplication(sys.argv)
    
    # 设置应用程序样式
    app.setStyle("Fusion")
    
    # 创建并显示主窗口
    window = MainWindow()
    window.show()
    
    # 运行应用程序
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
