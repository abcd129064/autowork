# -*- coding: utf-8 -*-
"""上传收集与打包 Worker

配合运维管理面板「设备状态」页的收集上传工作流：
    - CollectFilesWorker: 点击精度/问题后自动收集该设备的视频、日志、
      CPP 日志（daily_*.txt）与 detect.bin，复制到
      {videos_dir}/upload/{设备目录}/ 下。detect.bin 与 CPP 日志按
      「已存在即跳过」只收集一次；多个视频统一存放在同一设备目录下。
    - ZipUploadWorker: 将 upload 目录打包为 zip，通过 SFTP 上传到
      settings.json 配置的服务器目录，上传成功后清空本地 upload 目录。
"""

import os
import shutil
import zipfile
from datetime import datetime

from PySide6.QtCore import QThread, Signal


def clip_base_name(fname: str) -> str:
    """截取文件名 'kd' 之前的部分作为基础名（视频/日志同名）

    设备照片名形如 20260724_225031kd-200055-xxx.jpg，对应视频/日志为
    20260724_225031.mp4 / .log。不含 'kd' 的文件名取去扩展名部分。
    """
    fname = str(fname or "").strip()
    idx = fname.find("kd")
    if idx > 0:
        return fname[:idx]
    return os.path.splitext(fname)[0]


def date_from_base(base: str) -> str:
    """从基础名提取日期（20260724_225031 → 2026-07-24），无法解析返回空串"""
    d = str(base or "").split("_")[0]
    if len(d) == 8 and d.isdigit():
        return f"{d[0:4]}-{d[4:6]}-{d[6:8]}"
    return ""


class CollectFilesWorker(QThread):
    """异步收集设备视频/日志/CPP日志/detect.bin 到 upload 工作区

    已存在的目标文件直接跳过（重复点击不会重复复制），因此
    detect.bin / daily_*.txt 天然只收集一次。

    Signals:
        done(str, int, list): (设备目录名, 实际复制文件数, 缺失项说明列表)
        error(str): 错误信息
    """
    done = Signal(str, int, list)
    error = Signal(str)

    def __init__(self, videos_dir, device_id, base_names, parent=None):
        super().__init__(parent)
        self.videos_dir = videos_dir
        self.device_id = device_id
        self.base_names = list(base_names)

    def _copy_once(self, src, dst) -> int:
        """复制文件；目标已存在则跳过。返回 1=已复制 / 0=跳过"""
        if os.path.exists(dst):
            return 0
        shutil.copy2(src, dst)
        return 1

    def _find_first(self, candidates) -> str:
        for path in candidates:
            if os.path.isfile(path):
                return path
        return ""

    def run(self):
        try:
            device_dir = os.path.join(self.videos_dir, self.device_id)
            upload_dir = os.path.join(self.videos_dir, "upload", self.device_id)
            os.makedirs(upload_dir, exist_ok=True)

            copied = 0
            missing = []
            dates = set()

            for base in self.base_names:
                d = date_from_base(base)
                if d:
                    dates.add(d)

                # 视频：videos_dir/videos/{base}.mp4 优先，其次设备目录
                src_video = self._find_first([
                    os.path.join(self.videos_dir, "videos", base + ".mp4"),
                    os.path.join(device_dir, base + ".mp4"),
                ])
                if src_video:
                    copied += self._copy_once(
                        src_video, os.path.join(upload_dir, base + ".mp4"))
                else:
                    missing.append(f"{base}.mp4")

                # 日志：设备根目录优先，其次日期子目录
                log_cands = [os.path.join(device_dir, base + ".log")]
                if d:
                    log_cands.append(os.path.join(device_dir, d, base + ".log"))
                src_log = self._find_first(log_cands)
                if src_log:
                    copied += self._copy_once(
                        src_log, os.path.join(upload_dir, base + ".log"))
                else:
                    missing.append(f"{base}.log")

            # detect.bin 只收集一次（已存在跳过）
            src_bin = os.path.join(device_dir, "detect.bin")
            if os.path.isfile(src_bin):
                copied += self._copy_once(
                    src_bin, os.path.join(upload_dir, "detect.bin"))

            # CPP 日志 daily_{日期}.txt 按日期各收集一次
            for d in sorted(dates):
                src_daily = os.path.join(device_dir, f"daily_{d}.txt")
                if os.path.isfile(src_daily):
                    copied += self._copy_once(
                        src_daily, os.path.join(upload_dir, f"daily_{d}.txt"))

            self.done.emit(self.device_id, copied, missing)
        except Exception as e:
            self.error.emit(f"收集失败: {e}")


class ZipUploadWorker(QThread):
    """打包 upload 目录为 zip → SFTP 上传 → 清空本地 upload 目录

    凭据用上传专用字段 upload_user / upload_pass（不复用 SSH 凭据）；
    上传目标由 upload_host / upload_port / upload_remote_dir 配置。

    Signals:
        progress(str): 阶段提示（打包中/连接中/上传中）
        percent(int): 上传字节进度 0-100（SFTP put 回调驱动）
        done(str): 成功信息（zip 名与远端路径）
        error(str): 错误信息
    """
    progress = Signal(str)
    percent = Signal(int)
    done = Signal(str)
    error = Signal(str)

    def __init__(self, upload_root, host, port, username, password,
                 remote_dir, parent=None):
        super().__init__(parent)
        self.upload_root = upload_root
        self.host = host
        self.port = int(port or 22)
        self.username = username
        self.password = password
        self.remote_dir = (remote_dir or "").strip() or "/"
        self._last_percent = -1

    def _put_callback(self, transferred, total):
        """paramiko SFTP put 回调：字节进度去重后发 percent 信号"""
        if not total:
            return
        p = int(transferred * 100 / total)
        if p != self._last_percent:
            self._last_percent = p
            self.percent.emit(p)

    def _make_zip(self) -> str:
        """将 upload 目录打包为 videos_dir/upload_{时间戳}.zip（zip 内为设备目录结构）"""
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_path = os.path.join(
            os.path.dirname(self.upload_root), f"upload_{stamp}.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _dirs, files in os.walk(self.upload_root):
                for name in files:
                    full = os.path.join(root, name)
                    arc = os.path.relpath(full, self.upload_root)
                    zf.write(full, arc)
        return zip_path

    def _sftp_mkdirs(self, sftp, remote_dir):
        """尽力逐级创建远端目录（已存在则忽略）"""
        parts = [p for p in remote_dir.strip().split("/") if p]
        cur = "" if remote_dir.startswith("/") else "."
        for part in parts:
            cur = f"{cur}/{part}"
            try:
                sftp.stat(cur)
            except IOError:
                try:
                    sftp.mkdir(cur)
                except IOError:
                    pass

    def run(self):
        zip_path = ""
        try:
            if not os.path.isdir(self.upload_root) or not os.listdir(self.upload_root):
                self.error.emit("上传收集目录为空，请先点击精度/问题收集文件")
                return
            if not self.username or not self.password:
                self.error.emit("上传用户名/密码未配置，无法上传")
                return

            import paramiko

            self.progress.emit("正在打包 zip...")
            zip_path = self._make_zip()
            zip_name = os.path.basename(zip_path)

            self.progress.emit(f"正在连接 {self.host}:{self.port}...")
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(self.host, port=self.port, username=self.username,
                           password=self.password, timeout=15,
                           banner_timeout=15, auth_timeout=15)
            try:
                sftp = client.open_sftp()
                remote_path = f"{self.remote_dir.rstrip('/')}/{zip_name}"
                self.progress.emit(f"正在上传 {zip_name}...")
                self._last_percent = -1
                self.percent.emit(0)
                try:
                    sftp.put(zip_path, remote_path, callback=self._put_callback)
                except IOError:
                    # 远端目录可能不存在：尝试逐级创建后重试一次
                    self._sftp_mkdirs(sftp, self.remote_dir)
                    self._last_percent = -1
                    self.percent.emit(0)
                    sftp.put(zip_path, remote_path, callback=self._put_callback)
                self.percent.emit(100)
                sftp.close()
            finally:
                client.close()

            # 上传成功后清空本地收集目录
            shutil.rmtree(self.upload_root, ignore_errors=True)
            self.done.emit(f"{zip_name} → {self.host}:{remote_path}")
        except Exception as e:
            self.error.emit(f"打包上传失败: {e}")
