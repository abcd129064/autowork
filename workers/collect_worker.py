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


# ==================== 设备目录解析（C4：映射表持久化 + 模糊匹配） ====================

def norm_device_suffix(name: str) -> str:
    """后缀归一化：只留数字并去前导零（S8/08/TV2 → 8/8/2）"""
    digits = "".join(ch for ch in str(name or "") if ch.isdigit())
    return digits.lstrip("0")


def fuzzy_match_device_dir(videos_dir: str, candidates: list) -> tuple:
    """模糊搜索本地设备目录（命名与球桌号不一致时的兜底）

    规则：店号前缀（最后一个 '-' 之前）完全相同，后缀归一化后的
    数字相等；仅唯一命中才采用，多候选不猜。返回 (目录名, 匹配说明)。
    """
    try:
        entries = os.listdir(videos_dir)
    except OSError:
        return "", ""
    for cand in candidates:
        cand = str(cand or "").strip()
        if "-" not in cand:
            # 目录命名约定是「店号-机号」，拆不出前后缀就没法参与匹配
            continue
        prefix, suffix = cand.rsplit("-", 1)
        target = norm_device_suffix(suffix)
        if not target:
            # 后缀没数字时归一化结果是空串，空串互等会误匹配，直接跳过
            continue
        hits = []
        for name in entries:
            if "-" not in name:
                continue
            if not os.path.isdir(os.path.join(videos_dir, name)):
                continue
            p, s = name.rsplit("-", 1)
            if p == prefix and norm_device_suffix(s) == target:
                hits.append(name)
        if len(hits) == 1:
            return hits[0], f"{cand} → 匹配本地目录 {hits[0]}"
    return "", ""


def resolve_device_dir(videos_dir: str, candidates: list) -> tuple:
    """收集入口的设备目录三级解析（C4）

    查找顺序：
    ① device_mapping 表命中且目录仍存在 → 直接用（不再重新猜测）；
    ② table_id/device_code 精确同名目录 → 直接用（同名无需映射）；
    ③ 模糊匹配唯一命中 → 落库（source='auto'）后使用；
    均失败返回空（调用方走现有失败流程；自愈向导 Task #37 后续在
    此基础上人工选择落库，预留 source='manual'）。

    本函数在 QThread/主线程均可调用（table_db 单连接已设
    check_same_thread=False）；映射表读写异常静默降级为纯模糊匹配，
    不影响收集主流程。

    Returns:
        (device_id, note, source): 目录名 / 匹配说明（命中映射表时标注
        来源）/ 命中方式 ('mapping'|'exact'|'fuzzy'，失败为空串)
    """
    # 候选去重但保留顺序（dict.fromkeys），避免同一球桌号重复匹配、重复落库
    cands = list(dict.fromkeys(str(c or "").strip() for c in candidates if str(c or "").strip()))
    if not videos_dir or not cands:
        return "", "", ""
    # ① 映射表：任一候选命中且目录仍存在即采用
    try:
        from database import table_db
        for cand in cands:
            info = table_db.get_device_mapping(cand)
            local_dir = str(info.get("local_dir") or "").strip()
            if local_dir and os.path.isdir(os.path.join(videos_dir, local_dir)):
                # 映射可能过期（目录被删/改名），所以必须确认目录还在，
                # 失效映射穿透到后面两级重新匹配，而不是拿着死路径硬用
                src = str(info.get("source") or "auto")
                return local_dir, f"{cand} → 已存映射 {local_dir}（{src}）", "mapping"
    except Exception:
        pass
    # ② 精确同名目录
    for cand in cands:
        if os.path.isdir(os.path.join(videos_dir, cand)):
            return cand, "", "exact"
    # ③ 模糊匹配唯一命中 → 自动落库（失败静默，不阻断收集）
    device_id, note = fuzzy_match_device_dir(videos_dir, cands)
    if device_id:
        try:
            from database import table_db
            for cand in cands:
                table_db.set_device_mapping(cand, device_id, source="auto")
        except Exception:
            pass
        return device_id, note, "fuzzy"
    return "", "", ""


class FileCopyWorker(QThread):
    """异步文件拷贝 Worker（shutil.copy2 的线程替代）

    Signals:
        finished(): 拷贝成功完成
        error(str): 拷贝失败时的错误信息
    """
    copy_finished = Signal()
    error = Signal(str)

    def __init__(self, src, dst, parent=None):
        super().__init__(parent)
        self.src = src
        self.dst = dst

    def run(self):
        """执行 copy2，成功发 copy_finished，异常发 error"""
        try:
            shutil.copy2(self.src, self.dst)
            self.copy_finished.emit()
        except Exception as e:
            self.error.emit(str(e))


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
        """返回候选路径中第一个存在的文件，都没有则空串"""
        for path in candidates:
            if os.path.isfile(path):
                return path
        return ""

    def run(self):
        """按 base_names 逐项收集视频/日志，另收 detect.bin 与按日期的 daily 日志"""
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


class _UploadCancelled(Exception):
    """内部标记异常：打包/上传被用户取消（requestInterruption 后抛出）"""


class ZipUploadWorker(QThread):
    """打包 upload 目录为 zip → SFTP 上传 → 清空本地 upload 目录

    凭据用上传专用字段 upload_user / upload_pass（不复用 SSH 凭据）；
    上传目标由 upload_host / upload_port / upload_remote_dir 配置。

    取消支持（Task #57）：调用方 requestInterruption() 后，压缩循环
    （每 8 个文件检查一次）与 SFTP put 回调会检查中断标志并提前终止；
    取消后删除临时 zip、关闭 SFTP/SSH 连接，发 cancelled 信号。
    现有调用方不请求中断时行为与原先完全一致（向后兼容）。

    可选参数（批量整理等复用场景，均有兼容默认值）：
        content_root: 实际打包的源目录，默认 upload_root；
        zip_prefix:   zip 文件名前缀，默认 'upload'；
        zip_dir:      zip 落地目录，默认 upload_root 的上级目录；
        cleanup_after_done: 上传成功后是否清空 upload_root，默认 True；
        remove_zip_after_done: 上传成功后是否删除本地 zip，默认 False。

    Signals:
        progress(str): 阶段提示（打包中/连接中/上传中）
        percent(int): 上传字节进度 0-100（SFTP put 回调驱动）
        done(str): 成功信息（zip 名与远端路径）
        error(str): 错误信息
        cancelled(): 用户取消完成（临时 zip 已清理）
    """
    progress = Signal(str)
    percent = Signal(int)
    done = Signal(str)
    error = Signal(str)
    cancelled = Signal()

    def __init__(self, upload_root, host, port, username, password,
                 remote_dir, parent=None, content_root=None,
                 zip_prefix="upload", zip_dir=None,
                 cleanup_after_done=True, remove_zip_after_done=False):
        super().__init__(parent)
        self.upload_root = upload_root
        self.host = host
        self.port = int(port or 22)
        self.username = username
        self.password = password
        self.remote_dir = (remote_dir or "").strip() or "/"
        self.content_root = content_root  # 打包源目录；None 时等价 upload_root
        self.zip_prefix = zip_prefix or "upload"
        self.zip_dir = zip_dir  # zip 落地目录；None 时取 upload_root 上级目录
        self.cleanup_after_done = cleanup_after_done
        self.remove_zip_after_done = remove_zip_after_done
        self._last_percent = -1
        self._zip_path = ""

    def _put_callback(self, transferred, total):
        """paramiko SFTP put 回调：字节进度去重后发 percent 信号

        回调在 put 传输循环内逐块调用，此处抛异常可立即中断传输（取消上传）。
        """
        if self.isInterruptionRequested():
            raise _UploadCancelled()
        if not total:
            # 服务端没给文件总长时 total=0，算不了百分比，防除零直接 return
            return
        p = int(transferred * 100 / total)
        if p != self._last_percent:
            self._last_percent = p
            self.percent.emit(p)

    def _remove_temp_zip(self):
        """删除本次生成的临时 zip（取消时清理未完成的包，失败静默）"""
        if self._zip_path and os.path.exists(self._zip_path):
            try:
                os.remove(self._zip_path)
            except OSError:
                pass
        self._zip_path = ""

    def _make_zip(self) -> str:
        """将打包源目录压缩为 {zip_dir}/{前缀}_{时间戳}.zip（zip 内为源目录结构）

        压缩循环每 8 个文件检查一次中断标志，取消时删除未完成的 zip 并抛
        _UploadCancelled。
        """
        content_root = self.content_root or self.upload_root
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_dir = self.zip_dir or os.path.dirname(self.upload_root)
        os.makedirs(zip_dir, exist_ok=True)
        zip_path = os.path.join(zip_dir, f"{self.zip_prefix}_{stamp}.zip")
        self._zip_path = zip_path
        count = 0
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _dirs, files in os.walk(content_root):
                    for name in files:
                        count += 1
                        # 每 8 个文件检查一次取消请求，大文件包也能及时响应
                        if count % 8 == 0 and self.isInterruptionRequested():
                            raise _UploadCancelled()
                        full = os.path.join(root, name)
                        arc = os.path.relpath(full, content_root)
                        zf.write(full, arc)
        except _UploadCancelled:
            self._remove_temp_zip()
            raise
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
        sftp = None
        client = None
        try:
            content_root = self.content_root or self.upload_root
            if not os.path.isdir(content_root) or not os.listdir(content_root):
                self.error.emit("上传收集目录为空，请先点击精度/问题收集文件")
                return
            if not self.username or not self.password:
                self.error.emit("上传用户名/密码未配置，无法上传")
                return

            import paramiko

            self.progress.emit("正在打包 zip...")
            zip_path = self._make_zip()
            zip_name = os.path.basename(zip_path)

            # 打包完成到连接之间的间隙再检查一次，取消不必白跑连接
            if self.isInterruptionRequested():
                raise _UploadCancelled()

            self.progress.emit(f"正在连接 {self.host}:{self.port}...")
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(self.host, port=self.port, username=self.username,
                           password=self.password, timeout=15,
                           banner_timeout=15, auth_timeout=15)
            sftp = client.open_sftp()
            remote_path = f"{self.remote_dir.rstrip('/')}/{zip_name}"
            self.progress.emit(f"正在上传 {zip_name}...")
            self._last_percent = -1
            self.percent.emit(0)
            try:
                sftp.put(zip_path, remote_path, callback=self._put_callback)
            except IOError:
                if self.isInterruptionRequested():
                    raise _UploadCancelled()
                # 远端目录可能不存在：尝试逐级创建后重试一次
                self._sftp_mkdirs(sftp, self.remote_dir)
                self._last_percent = -1
                self.percent.emit(0)
                sftp.put(zip_path, remote_path, callback=self._put_callback)
            self.percent.emit(100)

            # 上传成功后按需清空本地收集目录（批量整理模式保留共享 upload 目录）
            if self.cleanup_after_done:
                shutil.rmtree(self.upload_root, ignore_errors=True)
            if self.remove_zip_after_done:
                self._remove_temp_zip()
            self.done.emit(f"{zip_name} → {self.host}:{remote_path}")
        except _UploadCancelled:
            self._remove_temp_zip()
            self.cancelled.emit()
        except Exception as e:
            # 取消请求发出后底层抛出的连接/传输异常一律按取消处理
            if self.isInterruptionRequested():
                self._remove_temp_zip()
                self.cancelled.emit()
            else:
                self.error.emit(f"打包上传失败: {e}")
        finally:
            # 无论成功/失败/取消，SFTP 与 SSH 连接资源都要关闭
            if sftp is not None:
                try:
                    sftp.close()
                except Exception:
                    pass
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
