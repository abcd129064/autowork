# -*- coding: utf-8 -*-
"""网络后台工作线程：TCP/SFTP/SSH 连接与操作。

本模块的类全部是 QThread 子类，界面层只管 new 出来 start()、连信号收结果。
paramiko 是同步阻塞库，一次 connect 可能卡十几秒，放进主线程会让窗口假死，
所以任何耗时可能超过 100ms 的远端操作都应该在这里开线程，而不是在主线程里直接调。
信号命名刻意避开 QThread 自带的 finished，否则同名会覆盖内置信号并导致崩溃。
"""

import os
import stat
import time
import threading
from datetime import datetime

from PySide6.QtCore import QThread, Signal

import paramiko

from core.conn_logger import conn_logger
from core.transfer_verify import (
    DIRECTION_DOWNLOAD, DIRECTION_UPLOAD, TransferSizeMismatch, check_transfer_size,
)
from core.utils import (
    classify_conn_error, safe_close_transport,
    RETRYABLE_KEYWORDS, RETRY_MAX, RETRY_DELAY,
)


class TCPWorker(QThread):
    """TCP 连接工作线程：连上后跑 hostname && whoami 验证凭据可用性。

只做一次连通性探测、不保留 client，适合「测试连接」这类一次性动作；
需要长期复用的连接用 SFTPConnectWorker / SSHConnectWorker。
"""
    # 注意：不能命名为 finished，会遮蔽 QThread 内置 finished 信号导致崩溃
    result_ready = Signal(str)
    error = Signal(str)

    def __init__(self, host, port, username, password):
        super().__init__()
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._client = None

    def run(self):
        """连接并执行 hostname/whoami 验证，结果经 result_ready 回传。

命令故意用 hostname 加 whoami 两条：既确认 shell 可用，
又把实际登录身份回显给用户——密码对应的账号和预期不符时一眼能看出来。
"""
        try:
            conn_logger.info('SSH', '开始连接', host=self.host, port=self.port, user=self.username)
            self._client = paramiko.SSHClient()
            self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self._client.connect(self.host, port=self.port,
                                 username=self.username, password=self.password,
                                 timeout=10, banner_timeout=15, auth_timeout=15)
            stdin, stdout, stderr = self._client.exec_command("hostname && whoami")
            result = stdout.read().decode('utf-8', errors='ignore').strip()
            conn_logger.info('SSH', f'连接验证成功: {result}', host=self.host, port=self.port, user=self.username)
            self.result_ready.emit(result)
        except Exception as e:
            conn_logger.exception('SSH', f'连接失败: {classify_conn_error(e)}', exc=e,
                                  host=self.host, port=self.port, user=self.username)
            self.error.emit(classify_conn_error(e))
        finally:
            # run() 结束后立即关闭 paramiko client，避免资源泄漏
            self.close()

    def close(self):
        """安全关闭 transport/client，幂等且不向外抛异常。

run() 的 finally 与外部主动断开都会调到这里，连接已断、transport 已为 None
的情况必须静默通过，不能因为清理失败盖掉真正有价值的连接错误。
"""
        if self._client:
            try:
                transport = self._client.get_transport()
                safe_close_transport(transport)
            except Exception:
                pass
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None


class SFTPListWorker(QThread):
    """异步 SFTP 列目录工作线程：在既有 transport 上开一条 sftp 子通道。

transport 是外部传入的共享连接，这里只借它开 channel、绝不 close，
否则会把同一会话里其它面板正在进行的传输一起踢掉。
"""
    result = Signal(str, list)
    error = Signal(str)

    def __init__(self, transport, remote_path):
        super().__init__()
        self.transport = transport
        self.remote_path = remote_path

    def run(self):
        """列目录并把条目整理成界面要的 dict，结果经 result 信号回传。

坏条目（st_mode / st_mtime 为空）降级成空值而不是整体失败：
某些服务端会返回残缺属性，丢一个字段总比整个目录列不出来强。
"""
        sftp = None
        try:
            # 为共享 transport 的 socket 设置超时，防止连接假死后列目录无限阻塞
            try:
                if hasattr(self.transport, 'sock') and self.transport.sock:
                    self.transport.sock.settimeout(30)
            except Exception:
                pass
            sftp = paramiko.SFTPClient.from_transport(self.transport)
            entries = []
            # from_transport 每次新开独立 session 通道，和别的 worker 共享同一条 transport 也不会互踢
            # 【性能关键】使用 listdir_attr 一次性获取所有文件属性（单次网络往返），
            # 避免逐文件 stat()（N 个文件 = N 次网络往返，大目录极慢）
            for attr in sftp.listdir_attr(self.remote_path):
                try:
                    is_dir = stat.S_ISDIR(attr.st_mode) if attr.st_mode else False
                    size = attr.st_size if attr.st_size else 0
                    mtime = datetime.fromtimestamp(attr.st_mtime).strftime('%Y-%m-%d %H:%M') if attr.st_mtime else ''
                    perm = stat.filemode(attr.st_mode) if attr.st_mode else ''
                except Exception:
                    is_dir, size, mtime, perm = False, 0, '', ''
                entries.append({
                    'name': attr.filename, 'is_dir': is_dir,
                    'size': size, 'mtime': mtime, 'perm': perm
                })
            self.result.emit(self.remote_path, entries)
        except Exception as e:
            conn_logger.exception('SFTP', f'列目录失败: {self.remote_path}', exc=e)
            self.error.emit(classify_conn_error(e))
        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass


class SFTPOperationWorker(QThread):
    """异步 SFTP 操作工作线程：上传/下载/删除/建目录/重命名，带进度与暂停。

每个操作都自建 transport、用完即关，因为这里可能被多个窗口并发调用，
共用连接时任意一方 close 都会把别人正在传的文件一起中断。
"""
    success = Signal(str)
    error = Signal(str)
    progress = Signal(int, int)  # (transferred_bytes, total_bytes)

    def __init__(self, conn_params, operation, local_path='', remote_path='', file_size=0,
                 verify_size=True):
        """绑定连接参数与操作类型；构造时不建连接，连接留到 run() 里做。

        构造函数跑在界面线程上，任何 paramiko 调用都可能把界面卡住，
        所以这里只存字段，绝不做网络动作。
        """
        super().__init__()
        self.conn_params = conn_params  # (host, port, username, password)
        self.operation = operation
        self.local_path = local_path
        self.remote_path = remote_path
        self.file_size = file_size
        # 传输完成后按两侧字节数校验（P2-2 步骤 1）：远端写满/中途断开时
        # paramiko 不报错，旧版会显示"已上传"而文件其实是截断的
        self.verify_size = verify_size
        # 暂停/停止控制
        self._pause_event = threading.Event()  # set=运行, clear=暂停
        self._pause_event.set()
        self._stop_flag = False

    def pause(self):
        """暂停传输，在下次进度回调处阻塞生效。

        用 Event.wait() 而不是循环 sleep：线程真正挂起，不空转 CPU。
        """
        self._pause_event.clear()

    def resume(self):
        """恢复已暂停的传输。"""
        self._pause_event.set()

    def stop(self):
        """取消传输：置停止标志并解除暂停阻塞，让线程尽快退出。

        必须先 set() 再等它自己退出：若线程正卡在 wait() 上，不解除就永远退不出来。
        """
        self._stop_flag = True
        self._pause_event.set()

    def _progress_cb(self, transferred, total):
        """paramiko 每次写块后的进度回调，同时是暂停与取消的唯一检查点。

        put/get 的复制循环没有取消接口，只能在这里抛异常打断，
        否则 stop 之后还得等整个文件传完才能停下来。
        """
        if self._stop_flag:
            raise InterruptedError('传输已取消')
        # 检查暂停（阻塞等待直到恢复）
        self._pause_event.wait()
        self.progress.emit(transferred, total)

    # ---------------- 传输完整性校验（P2-2 步骤 1） ----------------
    def _local_size(self):
        """本地文件字节数；取不到返回 -1，表示本次无法校验而不是判失败。

        上传前文件被删或被独占时 getsize 会失败，此时直接报错会把「没传成」
        误报成「传坏了」，所以交给调用方决定是否跳过校验。
        """
        try:
            return int(os.path.getsize(self.local_path))
        except OSError:
            return -1

    @staticmethod
    def _remote_size(sftp, path):
        """远端文件字节数；stat 失败返回 -1。

        远端 stat 失败多是权限或路径被改，与「大小不一致」是两回事，
        必须让调用方能区分，不能一律当成校验失败。
        """
        try:
            return int(sftp.stat(path).st_size)
        except Exception:
            return -1

    def _verify_upload(self, sftp, expected):
        """上传后核对：远端 stat 大小 == 传输前本地大小，不一致即抛错判失败。

        基准取传输前的本地大小，而不是传完再读一次：传的过程中本地文件可能被改，
        用传后的大小当基准会把一次正常的传输判成失败。
        """
        if not self.verify_size:
            return
        name = os.path.basename(self.local_path)
        if expected < 0:
            conn_logger.info('SFTP', '跳过上传大小校验：读不到本地文件大小',
                             host=self.conn_params[0], port=self.conn_params[1])
            return
        actual = self._remote_size(sftp, self.remote_path)
        if actual < 0:
            raise TransferSizeMismatch.unreadable(name, expected,
                                                 direction=DIRECTION_UPLOAD)
        check_transfer_size(expected, actual, name, direction=DIRECTION_UPLOAD)

    def _verify_download(self, sftp):
        """下载后核对：本地大小 == 远端 stat 大小，不一致即抛错判失败。

        远端大小现场 stat 而不是用构造时传入的 file_size：目录内容可能已经变了。
        """
        if not self.verify_size:
            return
        name = os.path.basename(self.remote_path)
        expected = self._remote_size(sftp, self.remote_path)
        actual = self._local_size()
        if expected < 0:
            # get() 内部本身就 stat 远端取大小，这里 stat 不到说明文件已被移走/权限变更
            raise TransferSizeMismatch.unreadable(name, self.file_size or 0,
                                                  direction=DIRECTION_DOWNLOAD,
                                                  side="远端")
        if actual < 0:
            raise TransferSizeMismatch.unreadable(name, expected,
                                                  direction=DIRECTION_DOWNLOAD,
                                                  side="本地")
        check_transfer_size(expected, actual, name, direction=DIRECTION_DOWNLOAD)

    def run(self):
        """按 operation 分发到对应的 sftp 操作，成功与失败各自发信号。

        每个分支只做一件事，异常统一冒到外层 except，由 classify_conn_error
        归一文案，免得每个分支各写一套提示。
        """
        transport = None
        sftp = None
        try:
            host, port, username, password = self.conn_params
            transport = paramiko.Transport((host, port))
            # banner/auth 单独设超时：默认无限制，坏网络下 connect 可能无限阻塞
            transport.banner_timeout = 15
            transport.auth_timeout = 15
            transport.connect(username=username, password=password)
            sftp = paramiko.SFTPClient.from_transport(transport)
            if self.operation == 'upload':
                expected = self._local_size()      # 传输前的本地大小 = 校验基准
                sftp.put(self.local_path, self.remote_path, callback=self._progress_cb)
                self._verify_upload(sftp, expected)
                self.success.emit(f"已上传: {os.path.basename(self.local_path)}")
            elif self.operation == 'download':
                sftp.get(self.remote_path, self.local_path, callback=self._progress_cb)
                self._verify_download(sftp)
                self.success.emit(f"已下载: {os.path.basename(self.remote_path)}")
            elif self.operation == 'delete':
                sftp.remove(self.remote_path)
                self.success.emit(f"已删除: {os.path.basename(self.remote_path)}")
            elif self.operation == 'rmdir':
                sftp.rmdir(self.remote_path)
                self.success.emit(f"已删除目录: {os.path.basename(self.remote_path)}")
            elif self.operation == 'mkdir':
                sftp.mkdir(self.remote_path)
                self.success.emit(f"已创建目录: {os.path.basename(self.remote_path)}")
            elif self.operation == 'rename':
                # local_path 复用为 old_path；posix_rename 是原子操作（覆盖存在目标），
                # 旧版 OpenSSH 不支持时回退普通 rename（目标存在则报错）
                try:
                    sftp.posix_rename(self.local_path, self.remote_path)
                except (AttributeError, IOError):
                    sftp.rename(self.local_path, self.remote_path)
                self.success.emit(f"已重命名: {os.path.basename(self.local_path)} -> {os.path.basename(self.remote_path)}")
            elif self.operation == 'create_file':
                with sftp.open(self.remote_path, 'w') as f:
                    pass
                self.success.emit(f"已创建文件: {os.path.basename(self.remote_path)}")
        except InterruptedError:
            # 用户主动取消不是错误：只记日志、不发 error 信号，界面不该弹红框
            conn_logger.info('SFTP', f'操作取消: {self.operation}',
                             host=self.conn_params[0], port=self.conn_params[1])
        except Exception as e:
            conn_logger.exception('SFTP', f'操作失败 [{self.operation}]', exc=e,
                                  host=self.conn_params[0], port=self.conn_params[1],
                                  user=self.conn_params[2])
            self.error.emit(classify_conn_error(e))
        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass
            safe_close_transport(transport)


class SFTPDirTransferWorker(QThread):
    """异步 SFTP 目录递归传输工作线程（上传整个目录/下载整个目录）"""
    success = Signal(str)
    error = Signal(str)
    progress = Signal(int, int)  # (transferred_bytes, total_bytes)

    def __init__(self, conn_params, operation, local_dir='', remote_dir='', dir_name='',
                 verify_size=True):
        """绑定目录传输参数与操作类型（upload_dir / download_dir）。

        构造期不建连接：它跑在界面线程上，任何网络动作都会把界面卡住。
        """
        super().__init__()
        self.conn_params = conn_params  # (host, port, username, password)
        self.operation = operation  # 'upload_dir' or 'download_dir'
        self.local_dir = local_dir
        self.remote_dir = remote_dir
        self.dir_name = dir_name
        self.verify_size = verify_size    # 逐文件比对两侧字节数（P2-2 步骤 1）
        # 暂停/停止控制
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stop_flag = False

    def pause(self):
        """暂停传输，在下一个文件开始前的检查点生效。

        目录传输只能在文件边界停，这是刻意取舍：逐块暂停会让已传一半的文件
        长期处于不完整状态，而下次续传并不认这个半成品。
        """
        self._pause_event.clear()

    def resume(self):
        """恢复已暂停的传输。"""
        self._pause_event.set()

    def stop(self):
        """取消传输：置停止标志并解除暂停阻塞。

        不强行 kill 线程，让它在下一个检查点自行退出，避免留下写了一半的文件句柄。
        """
        self._stop_flag = True
        self._pause_event.set()

    def _check_pause_stop(self):
        """检查停止/暂停状态：停止抛 InterruptedError，暂停则阻塞等待。

        每个文件开始前调一次，这是目录传输里唯一能让长任务响应用户操作的位置。
        """
        if self._stop_flag:
            raise InterruptedError('传输已取消')
        self._pause_event.wait()

    def run(self):
        """建立独立连接后按 operation 走上传目录或下载目录。

        这里是目录传输唯一的异常汇聚点：单文件失败已在 _upload_dir /
        _download_dir 内部收集成「部分失败」，能冒到这里的都是连接级错误。
        """
        transport = None
        sftp = None
        try:
            host, port, username, password = self.conn_params
            transport = paramiko.Transport((host, port))
            transport.banner_timeout = 15
            transport.auth_timeout = 15
            transport.connect(username=username, password=password)
            sftp = paramiko.SFTPClient.from_transport(transport)
            if self.operation == 'upload_dir':
                self._upload_dir(sftp)
            elif self.operation == 'download_dir':
                self._download_dir(sftp)
        except InterruptedError:
            conn_logger.info('SFTP', f'目录传输取消: {self.dir_name}',
                             host=self.conn_params[0], port=self.conn_params[1])
        except Exception as e:
            conn_logger.exception('SFTP', f'目录传输失败 [{self.dir_name}]', exc=e,
                                  host=self.conn_params[0], port=self.conn_params[1],
                                  user=self.conn_params[2])
            self.error.emit(f"目录传输失败 [{self.dir_name}]: {classify_conn_error(e)}")
        finally:
            if sftp:
                try:
                    sftp.close()
                except Exception:
                    pass
            safe_close_transport(transport)

    def _upload_dir(self, sftp):
        """递归上传本地目录到远程。

        先扫一遍收集清单与总大小，再逐文件传：进度条需要稳定的分母，
        边传边 stat 会让总大小不断跳变，进度条看起来像在倒退。
        """
        # 第一遍 walk：收集文件清单与总大小（作为进度条分母，避免逐文件实时 stat）
        total_size = 0
        file_list = []  # [(local_file_path, relative_path), ...]
        for root, dirs, files in os.walk(self.local_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, self.local_dir)
                try:
                    total_size += os.path.getsize(fpath)
                except OSError:
                    pass  # 个别文件读不到大小不阻塞整体，按 0 计
                file_list.append((fpath, rel))

        transferred = 0
        errors = []
        # 先建远程根目录，再预建全部子目录：
        # 否则逐文件 put 时 sftp 不会自动创建中间目录，会全部报错
        self._mkdir_p_remote(sftp, self.remote_dir)
        for root, dirs, files in os.walk(self.local_dir):
            for dname in dirs:
                dpath = os.path.join(root, dname)
                rel_dir = os.path.relpath(dpath, self.local_dir).replace('\\', '/')
                remote_sub = self.remote_dir.rstrip('/') + '/' + rel_dir
                self._mkdir_p_remote(sftp, remote_sub)

        # 逐文件上传：相对路径原样拼到远程根目录，保持目录结构一致
        for fpath, rel in file_list:
            self._check_pause_stop()
            rel_remote = rel.replace('\\', '/')
            remote_file = self.remote_dir.rstrip('/') + '/' + rel_remote
            try:
                file_size = os.path.getsize(fpath)
                sftp.put(fpath, remote_file)
                self._verify_dir_file(sftp, remote_file, file_size, rel,
                                      direction=DIRECTION_UPLOAD)
                transferred += file_size
                self.progress.emit(transferred, total_size)
            except TransferSizeMismatch as e:
                errors.append(str(e))
                self.progress.emit(transferred, total_size)
            except PermissionError as e:
                errors.append(f"权限不足: {rel} ({e})")
            except OSError as e:
                errors.append(f"文件占用/不可读: {rel} ({e})")
            except Exception as e:
                errors.append(f"传输失败: {rel} ({e})")

        if errors:
            err_summary = '; '.join(errors[:5])
            if len(errors) > 5:
                err_summary += f'（另有 {len(errors) - 5} 个错误未列出）'
            self.error.emit(f"目录上传部分失败 [{self.dir_name}]: {err_summary}")
        else:
            self.success.emit(f"已上传目录: {self.dir_name} ({len(file_list)} 个文件)")

    def _download_dir(self, sftp):
        """递归下载远程目录到本地。

        远程清单必须一次收全：下载期间目录内容可能变化，中途再列会把同一个
        文件既算进总数又被重复下载。
        """
        # 先递归收集远程文件列表及总大小
        file_list = []  # [(remote_file_path, relative_path, size), ...]
        total_size = 0
        self._collect_remote_files(sftp, self.remote_dir, '', file_list)
        for _, _, sz in file_list:
            total_size += sz

        transferred = 0
        errors = []
        # 创建本地根目录
        try:
            os.makedirs(self.local_dir, exist_ok=True)
        except OSError as e:
            self.error.emit(f"无法创建本地目录 [{self.local_dir}]: {e}")
            return

        # 逐文件下载
        for remote_file, rel, sz in file_list:
            self._check_pause_stop()
            local_file = os.path.join(self.local_dir, rel.replace('/', os.sep))
            local_sub_dir = os.path.dirname(local_file)
            try:
                os.makedirs(local_sub_dir, exist_ok=True)
                sftp.get(remote_file, local_file)
                try:
                    local_now = os.path.getsize(local_file)
                except OSError:
                    local_now = -1
                self._verify_dir_file(sftp, remote_file, local_now, rel,
                                      direction=DIRECTION_DOWNLOAD)
                transferred += sz
                self.progress.emit(transferred, total_size)
            except TransferSizeMismatch as e:
                errors.append(str(e))
                self.progress.emit(transferred, total_size)
            except PermissionError as e:
                errors.append(f"权限不足: {rel} ({e})")
            except OSError as e:
                errors.append(f"目标不可写/路径不存在: {rel} ({e})")
            except Exception as e:
                errors.append(f"传输失败: {rel} ({e})")

        if errors:
            err_summary = '; '.join(errors[:5])
            if len(errors) > 5:
                err_summary += f'（另有 {len(errors) - 5} 个错误未列出）'
            self.error.emit(f"目录下载部分失败 [{self.dir_name}]: {err_summary}")
        else:
            self.success.emit(f"已下载目录: {self.dir_name} ({len(file_list)} 个文件)")

    @staticmethod
    def _remote_stat_size(sftp, path):
        """远端文件字节数；stat 失败返回 -1。

        逐文件校验时远端可能已被别人删掉，这种情况按「无法校验」跳过，
        由后续操作报错，比在这里直接判失败更容易定位。
        """
        try:
            return int(sftp.stat(path).st_size)
        except Exception:
            return -1

    def _verify_dir_file(self, sftp, remote_file, local_size, rel, *, direction):
        """目录传输逐文件校验（P2-2 步骤 1）

        不一致时抛 TransferSizeMismatch，由调用方记进 errors 汇总成"部分失败"，
        不让单个截断文件被算成成功。local_size<0 或远端 stat 失败时跳过校验。
        """
        if not self.verify_size:
            return
        remote_size = self._remote_stat_size(sftp, remote_file)
        if remote_size < 0 or int(local_size) < 0:
            conn_logger.info('SFTP', f'跳过大小校验: {rel}',
                             host=self.conn_params[0], port=self.conn_params[1])
            return
        if direction == DIRECTION_UPLOAD:
            check_transfer_size(local_size, remote_size, rel, direction=direction)
        else:
            check_transfer_size(remote_size, local_size, rel, direction=direction)

    def _collect_remote_files(self, sftp, remote_base, rel_prefix, file_list):
        """递归收集远程目录下所有文件，产出 (远端全路径, 相对路径, 大小)。

        listdir_attr 失败时静默返回：没有权限的子目录直接跳过，
        不影响其它能读的目录继续下载。
        """
        try:
            entries = sftp.listdir_attr(remote_base)
        except Exception:
            return
        for attr in entries:
            name = attr.filename
            full_path = remote_base.rstrip('/') + '/' + name
            rel_path = (rel_prefix + '/' + name) if rel_prefix else name
            if stat.S_ISDIR(attr.st_mode) if attr.st_mode else False:
                self._collect_remote_files(sftp, full_path, rel_path, file_list)
            else:
                size = attr.st_size if attr.st_size else 0
                file_list.append((full_path, rel_path, size))

    def _mkdir_p_remote(self, sftp, remote_path):
        """递归创建远程目录（类似 mkdir -p）。

        先向上找到第一个已存在的祖先，再反向逐级 mkdir：SFTP 的 mkdir
        不会创建中间目录，直接建深层路径会整条失败。
        """
        dirs_to_create = []
        path = remote_path
        while path and path != '/':
            try:
                sftp.stat(path)
                break  # 已存在
            except FileNotFoundError:
                dirs_to_create.append(path)
                path = '/'.join(path.rstrip('/').split('/')[:-1])
                if not path:
                    path = '/'
            except IOError:
                dirs_to_create.append(path)
                path = '/'.join(path.rstrip('/').split('/')[:-1])
                if not path:
                    path = '/'
        for d in reversed(dirs_to_create):
            try:
                sftp.mkdir(d)
            except Exception:
                pass  # 可能已被并发创建


class _BaseConnectWorker(QThread):
    """连接 Worker 基类：统一重试、中止与错误归一，子类只提供连接细节。

    几个连接 Worker 的重试策略必须一致（同一份 RETRY_MAX / RETRY_DELAY 与
    可重试关键字），所以循环提到基类，避免各写一份后行为慢慢漂移。
    """
    connected = Signal(object)
    error = Signal(str)

    def __init__(self, host, port, username, password):
        """只保存连接参数，不建连接；连接动作留给子类的 _do_connect。

        界面线程会直接 new 这个对象，构造期间不能有任何阻塞调用。
        """
        super().__init__()
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._abort = False

    def abort(self):
        """请求中止重试循环；线程会在下一个检查点自行退出。

        不 join 等待：中止发生在关窗路径上，卡住界面比留下一个线程更糟。
        """
        self._abort = True

    # ---- 子类必须覆盖 ----
    @property
    def _log_name(self):
        """日志里的模块名（SFTP / SSH）；子类必须覆盖。

        日志按模块名分栏，缺了它所有连接会被归到同一栏，排查时无法区分。
        """
        raise NotImplementedError

    def _do_connect(self):
        """执行实际连接并返回连接对象；子类必须覆盖。

        返回的对象经 connected 信号交给调用方长期持有。
        """
        raise NotImplementedError

    def _safe_close(self):
        """释放上一次失败尝试留下的连接；子类必须覆盖。

        重试前不清理会累积半开连接，直到服务端连接数被打满。
        """
        raise NotImplementedError

    def _log_before_connect(self, attempt):
        """连接前记日志（默认空实现，SSH 子类覆盖）。

        SFTP 连接在别处已有统一日志，只有 SSH 需要按尝试次数逐次记录。
        """
        pass

    # ---- 通用重试循环 ----
    def run(self):
        """重试循环：可重试错误退避重试，确定性错误立即终报。

        每次重试都重新调 _do_connect，因此用的是全新连接对象；复用上一条
        失败连接只会让错误更诡异——半开 transport 上的一切操作都不可信。
        """
        for attempt in range(1, RETRY_MAX + 1):
            # 用户取消（窗口关闭/手动中止）优先于重试逻辑
            if self._abort:
                conn_logger.info(self._log_name, '连接已中止（用户取消）',
                                 host=self.host, port=self.port, user=self.username)
                return
            self._log_before_connect(attempt)
            try:
                obj = self._do_connect()
                conn_logger.info(self._log_name, f'连接成功 (第{attempt}次尝试)',
                                 host=self.host, port=self.port, user=self.username)
                self.connected.emit(obj)
                return
            except Exception as e:
                # 失败后先安全释放资源，避免残留连接句柄泄漏
                self._safe_close()
                err_msg = str(e)
                friendly = classify_conn_error(e)
                # 仅对「可重试」类错误（超时/拒绝等）继续重试，
                # 认证失败等确定性错误重试也无意义，直接终报
                if any(kw in err_msg for kw in RETRYABLE_KEYWORDS) and attempt < RETRY_MAX:
                    conn_logger.error(self._log_name,
                                      f'连接失败，将重试 ({attempt}/{RETRY_MAX}): {err_msg}',
                                      host=self.host, port=self.port, user=self.username,
                                      error_type=type(e).__name__)
                    time.sleep(RETRY_DELAY)  # 间隔后进入下一轮重试
                    continue
                # 退避期间用户也可能关窗，这里再查一次，避免对着已销毁的界面发信号
                if self._abort:
                    return
                conn_logger.exception(self._log_name,
                                      f'连接最终失败 (已尝试{attempt}次): {friendly}', exc=e,
                                      host=self.host, port=self.port, user=self.username)
                # 提示信息区分「重试过多次」与「首次即败」，便于用户判断问题性质
                if attempt > 1:
                    self.error.emit(f'连接失败（已重试{RETRY_MAX}次）: {friendly}')
                else:
                    self.error.emit(friendly)
                return


class SFTPConnectWorker(_BaseConnectWorker):
    """异步建立 paramiko.Transport（SFTP 通道）连接，带自动重试。

    连上后 transport 交给调用方共享，本类只负责建立与保活，不负责关闭。
    """

    def __init__(self, host, port, username, password):
        """记录 transport 占位：run() 里创建，_safe_close 里清空。"""
        super().__init__(host, port, username, password)
        self._transport = None

    @property
    def _log_name(self):
        """日志模块名，固定为 SFTP。

        常量也要走属性，基类才能统一按名取日志分栏。
        """
        return 'SFTP'

    def _do_connect(self):
        """建立 transport 并完成认证，同时开启 30 秒保活。

        保活包不能省：NAT 与防火墙会掐掉长时间空闲的连接，
        面板放着不动几十分钟，再点一下就成了「连接已断开」。
        """
        self._transport = paramiko.Transport((self.host, self.port))
        self._transport.banner_timeout = 15
        self._transport.auth_timeout = 15
        self._transport.connect(username=self.username, password=self.password)
        self._transport.set_keepalive(30)
        return self._transport

    def _safe_close(self):
        """关闭 transport 并清空引用。

        重试前必须先关：否则每条失败尝试都会留下一个半开连接。
        """
        safe_close_transport(self._transport)
        self._transport = None


class SSHConnectWorker(_BaseConnectWorker):
    """异步建立 paramiko.SSHClient 连接，带自动重试与保活。

    与 SFTPConnectWorker 的区别是保留完整 client：后续 exec_command 要用它。
    """

    def __init__(self, host, port, username, password):
        """记录 client 占位，实际连接在 run() 里完成。

        构造期不做网络动作：它跑在界面线程上。
        """
        super().__init__(host, port, username, password)
        self._client = None

    @property
    def _log_name(self):
        """日志模块名，固定为 SSH。

        与 SFTP 分栏输出，同一次操作里的连接日志与传输日志才不会混在一起。
        """
        return 'SSH'

    def _log_before_connect(self, attempt):
        """每次尝试前记一条日志。

        重试全部失败时，日志里能看到第几次、什么时间试的，
        比只留一条最终错误更容易判断是网络抖动还是凭据问题。
        """
        conn_logger.info('SSH', f'尝试连接 ({attempt}/{RETRY_MAX})',
                         host=self.host, port=self.port, user=self.username)

    def _do_connect(self):
        """建立 SSHClient 并开启保活，成功返回 client 实例。

        host key 用 AutoAddPolicy 直接接受：本工具面向内网自有机器，
        弹确认框会让自动重试卡在无人值守的界面上。
        """
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            self.host, port=self.port,
            username=self.username, password=self.password,
            timeout=10, banner_timeout=15, auth_timeout=15
        )
        transport = client.get_transport()
        if transport:
            transport.set_keepalive(30)
        self._client = client
        return client

    def _safe_close(self):
        """关闭 client 与 transport；任一环节失败都不影响重试流程。

        清理动作本身出错没有意义——连接已经坏了，重试会从干净状态开始。
        """
        if self._client:
            try:
                transport = self._client.get_transport()
                safe_close_transport(transport)
            except Exception:
                pass
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None


class SSHExecWorker(QThread):
    """异步执行 SSH 命令的工作线程（使用 exec_command，无持久 shell）"""
    output = Signal(str)
    error = Signal(str)
    # 注意：不能命名为 finished，会遮蔽 QThread 内置 finished 信号导致崩溃
    done = Signal()

    def __init__(self, client, command):
        super().__init__()
        self._client = client
        self._command = command

    def run(self):
        try:
            stdin, stdout, stderr = self._client.exec_command(self._command, timeout=30)
            # 通道级超时兜底：连接假死时 read() 不会无限阻塞（120s 无数据视为异常）
            try:
                stdout.channel.settimeout(120)
            except Exception:
                pass
            out = stdout.read().decode('utf-8', errors='ignore')
            err = stderr.read().decode('utf-8', errors='ignore')
            if out:
                self.output.emit(out)
            if err:
                self.error.emit(err)
        except Exception as e:
            conn_logger.exception('SSH-EXEC', f'命令执行失败: {self._command[:100]}', exc=e)
            self.error.emit(f'[命令执行异常] {classify_conn_error(e)}')
        finally:
            self.done.emit()

