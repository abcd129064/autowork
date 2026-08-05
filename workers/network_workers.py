# -*- coding: utf-8 -*-
"""网络后台工作线程：TCP/SFTP/SSH 连接与操作"""

import os
import stat
import time
import threading
from datetime import datetime

from PySide6.QtCore import QThread, Signal

import paramiko

from core.conn_logger import conn_logger
from core.utils import (
    classify_conn_error, safe_close_transport,
    RETRYABLE_KEYWORDS, RETRY_MAX, RETRY_DELAY,
)


class TCPWorker(QThread):
    """TCP 连接工作线程"""
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
    """异步 SFTP 列目录工作线程"""
    result = Signal(str, list)
    error = Signal(str)

    def __init__(self, transport, remote_path):
        super().__init__()
        self.transport = transport
        self.remote_path = remote_path

    def run(self):
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
    """异步 SFTP 操作工作线程（上传/下载/删除/创建目录），支持传输进度"""
    success = Signal(str)
    error = Signal(str)
    progress = Signal(int, int)  # (transferred_bytes, total_bytes)

    def __init__(self, conn_params, operation, local_path='', remote_path='', file_size=0):
        super().__init__()
        self.conn_params = conn_params  # (host, port, username, password)
        self.operation = operation
        self.local_path = local_path
        self.remote_path = remote_path
        self.file_size = file_size
        # 暂停/停止控制
        self._pause_event = threading.Event()  # set=运行, clear=暂停
        self._pause_event.set()
        self._stop_flag = False

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def stop(self):
        self._stop_flag = True
        self._pause_event.set()  # 解除暂停阻塞以便线程退出

    def _progress_cb(self, transferred, total):
        # 检查停止
        if self._stop_flag:
            raise InterruptedError('传输已取消')
        # 检查暂停（阻塞等待直到恢复）
        self._pause_event.wait()
        self.progress.emit(transferred, total)

    def run(self):
        transport = None
        sftp = None
        try:
            host, port, username, password = self.conn_params
            transport = paramiko.Transport((host, port))
            transport.banner_timeout = 15
            transport.auth_timeout = 15
            transport.connect(username=username, password=password)
            sftp = paramiko.SFTPClient.from_transport(transport)
            if self.operation == 'upload':
                sftp.put(self.local_path, self.remote_path, callback=self._progress_cb)
                self.success.emit(f"已上传: {os.path.basename(self.local_path)}")
            elif self.operation == 'download':
                sftp.get(self.remote_path, self.local_path, callback=self._progress_cb)
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
                # local_path 复用为 old_path
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

    def __init__(self, conn_params, operation, local_dir='', remote_dir='', dir_name=''):
        super().__init__()
        self.conn_params = conn_params  # (host, port, username, password)
        self.operation = operation  # 'upload_dir' or 'download_dir'
        self.local_dir = local_dir
        self.remote_dir = remote_dir
        self.dir_name = dir_name
        # 暂停/停止控制
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._stop_flag = False

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def stop(self):
        self._stop_flag = True
        self._pause_event.set()

    def _check_pause_stop(self):
        """检查停止/暂停状态，停止时抛出InterruptedError，暂停时阻塞等待"""
        if self._stop_flag:
            raise InterruptedError('传输已取消')
        self._pause_event.wait()

    def run(self):
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
        """递归上传本地目录到远程"""
        # 先计算总大小
        total_size = 0
        file_list = []  # [(local_file_path, relative_path), ...]
        for root, dirs, files in os.walk(self.local_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                rel = os.path.relpath(fpath, self.local_dir)
                try:
                    total_size += os.path.getsize(fpath)
                except OSError:
                    pass
                file_list.append((fpath, rel))

        transferred = 0
        errors = []
        # 创建远程根目录
        self._mkdir_p_remote(sftp, self.remote_dir)
        # 预创建所有子目录
        for root, dirs, files in os.walk(self.local_dir):
            for dname in dirs:
                dpath = os.path.join(root, dname)
                rel_dir = os.path.relpath(dpath, self.local_dir).replace('\\', '/')
                remote_sub = self.remote_dir.rstrip('/') + '/' + rel_dir
                self._mkdir_p_remote(sftp, remote_sub)

        # 逐文件上传
        for fpath, rel in file_list:
            self._check_pause_stop()
            rel_remote = rel.replace('\\', '/')
            remote_file = self.remote_dir.rstrip('/') + '/' + rel_remote
            try:
                file_size = os.path.getsize(fpath)
                sftp.put(fpath, remote_file)
                transferred += file_size
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
                err_summary += f' ...等共{len(errors)}个错误'
            self.error.emit(f"目录上传部分失败 [{self.dir_name}]: {err_summary}")
        else:
            self.success.emit(f"已上传目录: {self.dir_name} ({len(file_list)} 个文件)")

    def _download_dir(self, sftp):
        """递归下载远程目录到本地"""
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
                transferred += sz
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
                err_summary += f' ...等共{len(errors)}个错误'
            self.error.emit(f"目录下载部分失败 [{self.dir_name}]: {err_summary}")
        else:
            self.success.emit(f"已下载目录: {self.dir_name} ({len(file_list)} 个文件)")

    def _collect_remote_files(self, sftp, remote_base, rel_prefix, file_list):
        """递归收集远程目录下的所有文件"""
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
        """递归创建远程目录（类似 mkdir -p）"""
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
    """连接 Worker 基类，封装重试逻辑"""
    connected = Signal(object)
    error = Signal(str)

    def __init__(self, host, port, username, password):
        super().__init__()
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self._abort = False

    def abort(self):
        """请求中止重试循环"""
        self._abort = True

    # ---- 子类必须覆盖 ----
    @property
    def _log_name(self):
        raise NotImplementedError

    def _do_connect(self):
        """执行实际连接，返回连接对象"""
        raise NotImplementedError

    def _safe_close(self):
        """安全关闭连接"""
        raise NotImplementedError

    def _log_before_connect(self, attempt):
        """连接前的日志（默认无操作，SSH 子类覆盖）"""
        pass

    # ---- 通用重试循环 ----
    def run(self):
        for attempt in range(1, RETRY_MAX + 1):
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
                self._safe_close()
                err_msg = str(e)
                friendly = classify_conn_error(e)
                if any(kw in err_msg for kw in RETRYABLE_KEYWORDS) and attempt < RETRY_MAX:
                    conn_logger.error(self._log_name,
                                      f'连接失败，将重试 ({attempt}/{RETRY_MAX}): {err_msg}',
                                      host=self.host, port=self.port, user=self.username,
                                      error_type=type(e).__name__)
                    time.sleep(RETRY_DELAY)
                    continue
                if self._abort:
                    return
                conn_logger.exception(self._log_name,
                                      f'连接最终失败 (已尝试{attempt}次): {friendly}', exc=e,
                                      host=self.host, port=self.port, user=self.username)
                if attempt > 1:
                    self.error.emit(f'连接失败（已重试{RETRY_MAX}次）: {friendly}')
                else:
                    self.error.emit(friendly)
                return


class SFTPConnectWorker(_BaseConnectWorker):
    """异步建立 paramiko.Transport 连接的工作线程（含自动重试）"""

    def __init__(self, host, port, username, password):
        super().__init__(host, port, username, password)
        self._transport = None

    @property
    def _log_name(self):
        return 'SFTP'

    def _do_connect(self):
        self._transport = paramiko.Transport((self.host, self.port))
        self._transport.banner_timeout = 15
        self._transport.auth_timeout = 15
        self._transport.connect(username=self.username, password=self.password)
        self._transport.set_keepalive(30)
        return self._transport

    def _safe_close(self):
        safe_close_transport(self._transport)
        self._transport = None


class SSHConnectWorker(_BaseConnectWorker):
    """异步建立 SSH 连接的工作线程（保持 client 存活，含自动重试）"""

    def __init__(self, host, port, username, password):
        super().__init__(host, port, username, password)
        self._client = None

    @property
    def _log_name(self):
        return 'SSH'

    def _log_before_connect(self, attempt):
        conn_logger.info('SSH', f'尝试连接 ({attempt}/{RETRY_MAX})',
                         host=self.host, port=self.port, user=self.username)

    def _do_connect(self):
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

