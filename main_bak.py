# -*- coding: utf-8 -*-
import sys
import os
import json
import re
import subprocess
from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox
from PySide6.QtCore import Slot, QProcess
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
    
    def init_ui(self):
        """初始化UI组件"""
        # 设置默认日期为当前日期
        from PySide6.QtCore import QDate
        self.ui.date.setDate(QDate.currentDate())
        
        # 初始化程序下拉框 - 扫描 snooker/bin64 目录下的 SnookerTracking*.exe
        self._load_exe_list()
        
        # 初始化设备代码列表 - 扫描 videos 目录下的设备文件夹
        self._load_device_list()
        
        # 在日志区域显示欢迎信息
        self.ui.show_log.appendPlainText("欢迎使用 AutoWork 工具！")
        self.ui.show_log.appendPlainText("请选择程序并开始工作...")
        
        # 存储当前选中的视频和帧数
        self.current_video = None
        self.current_frame = None
        
        # 存储运行的程序进程
        self.running_process = None
    
    def _load_exe_list(self):
        """加载 snooker/bin64 目录下的 SnookerTracking*.exe 到程序下拉框"""
        import os
        import glob
        
        exe_dir = r"C:\Users\shen_zhe\Desktop\snooker\bin64"
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
        import os
        
        videos_dir = r"C:\Users\shen_zhe\Desktop\videos"
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
        import os
        import glob
        
        videos_dir = r"C:\Users\shen_zhe\Desktop\videos"
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
        self.ui.write_table.clicked.connect(self.on_write_table_clicked)
        # 列表项选择事件
        self.ui.id_list.itemClicked.connect(self.on_id_selected)
        self.ui.loacl_video_list.itemClicked.connect(self.on_video_selected)
        self.ui.log_list.itemClicked.connect(self.on_log_selected)
        self.ui.log_list.itemDoubleClicked.connect(self.on_log_double_clicked)
        
        # 日期改变时重新加载第二列
        self.ui.date.dateChanged.connect(self._on_date_changed)
    
    @Slot()
    def on_flush_clicked(self):
        """刷新按钮点击事件"""
        self.ui.show_log.appendPlainText("\n[操作] 刷新数据...")
        
        # 先记住当前选中的设备代码
        current_device = self.ui.id_list.currentItem()
        saved_device_code = current_device.text() if current_device else None
        
        # 1. 重新扫描可执行程序下拉框
        self._load_exe_list()
        
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
        
        exe_dir = r"C:\Users\shen_zhe\Desktop\snooker\bin64"
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
        
        # 启动程序
        self.running_process.start(exe_path)
        self.ui.show_log.appendPlainText(f"\n[播放] 已启动程序: {exe_name}")
        self.ui.show_log.appendPlainText(f"  - 工作目录: {exe_dir}")
    
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
        
        # 如果勾选了复制信息
        if self.ui.end_copy_info.isChecked():
            self.ui.show_log.appendPlainText("[信息] 已复制到剪贴板")
            # TODO: 实现复制信息到剪贴板的逻辑
        
    @Slot()
    def on_open_daily_clicked(self):
        """现场日志按钮点击事件"""
        self.ui.show_log.appendPlainText("\n[日志] 打开现场日志文件")
        # TODO: 实现打开日志文件的逻辑
        
    @Slot()
    def on_write_table_clicked(self):
        """导出表按钮点击事件"""
        self.ui.show_log.appendPlainText("\n[导出] 导出数据到表格")
        # TODO: 实现导出Excel或CSV的逻辑
        
    @Slot()
    def on_id_selected(self, item):
        """ID列表项选中事件 - 加载对应设备的日志目录"""
        device_code = item.text()
        self.ui.show_log.appendPlainText(f"\n[设备选中] {device_code}")
        
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
        full_log_path = os.path.join(r"C:\Users\shen_zhe\Desktop\videos", device_code, date_str, log_filename)
        
        # 读取日志文件内容并显示在第三列
        try:
            with open(full_log_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            self.ui.log_list.clear()
            for line in content.splitlines():
                self.ui.log_list.addItem(line)
            
            self.ui.show_log.appendPlainText(f"[日志内容] 已加载 {len(content.splitlines())} 行")
        except Exception as e:
            self.ui.show_log.appendPlainText(f"[错误] 无法读取日志文件: {str(e)}")
            self.ui.log_list.clear()
        
    @Slot()
    def on_log_selected(self, item):
        """日志列表项选中事件 - 解析日志并更新cfg.json"""
        log_line = item.text()
        self.ui.show_log.appendPlainText(f"\n[日志选中] {log_line}")
        
        # 解析日志：提取帧数和视频文件名
        # 格式示例：2025-12-21 14:37:03 frame_id:7540 选手1 进6球，目标球1，红球12，需要
        frame_match = re.search(r'frame_id:(\d+)', log_line)
        if not frame_match:
            self.ui.show_log.appendPlainText("[警告] 日志中未找到 frame_id")
            return
        
        frame = int(frame_match.group(1))
        self.current_frame = frame
        
        # 获取当前选中的设备代码
        if not self.ui.id_list.currentItem():
            self.ui.show_log.appendPlainText("[警告] 未选择设备代码")
            return
        
        device_code = self.ui.id_list.currentItem().text()
        
        # 从第二列（日志目录）获取当前选中的日志文件路径
        if not self.ui.loacl_video_list.currentItem():
            self.ui.show_log.appendPlainText("[警告] 未选择日志文件")
            return
        
        log_filename = self.ui.loacl_video_list.currentItem().text()
        # 从日志文件名推断视频文件名
        # 例如：20260705_131009.log / 20251221_143148.txt -> 20260705_131009.mp4
        video_name = os.path.splitext(log_filename)[0] + '.mp4'
        
        # 构建视频完整路径
        video_path = f"C:/Users/shen_zhe/Desktop/videos/videos/{video_name}"
        self.current_video = video_path
        
        # 更新 cfg.json
        cfg_path = r"C:\Users\shen_zhe\Desktop\snooker\bin64\cfg.json"
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                
                # 修改 cap.file 下的配置
                if 'cap' in cfg and 'file' in cfg['cap']:
                    cfg['cap']['file']['path'] = video_path
                    cfg['cap']['file']['video_start_frame'] = frame
                
                # 删除根级别的 path 和 video_start_frame（如果存在）
                if 'path' in cfg:
                    del cfg['path']
                if 'video_start_frame' in cfg:
                    del cfg['video_start_frame']
                
                with open(cfg_path, 'w', encoding='utf-8') as f:
                    json.dump(cfg, f, ensure_ascii=False, indent=2)
                
                self.ui.show_log.appendPlainText(f"[配置] 已更新 cfg.json")
                self.ui.show_log.appendPlainText(f"  - 视频: {video_path}")
                self.ui.show_log.appendPlainText(f"  - 帧数: {frame}")
            except Exception as e:
                self.ui.show_log.appendPlainText(f"[错误] 更新 cfg.json 失败: {str(e)}")
                import traceback
                self.ui.show_log.appendPlainText(traceback.format_exc())
        else:
            self.ui.show_log.appendPlainText(f"[警告] cfg.json 不存在: {cfg_path}")

    def _on_date_changed(self, date):
        """日期改变时重新加载第二列日志列表"""
        current_device = self.ui.id_list.currentItem()
        if current_device:
            device_code = current_device.text()
            self._load_videos_for_device(device_code)
            self.ui.log_list.clear()

    @Slot()
    def on_log_double_clicked(self, item):
        """日志列表项双击事件 - 解析日志、更新cfg.json并启动程序"""
        # 先触发选中逻辑（更新cfg.json）
        self.on_log_selected(item)
        
        # 然后启动播放
        self.on_start_clicked()


def main():
    """主函数"""
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
