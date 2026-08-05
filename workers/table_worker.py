# -*- coding: utf-8 -*-
"""球桌数据 API 异步请求 Worker

包含三个 Worker：
    - TableFetchWorker: 球桌列表接口 wechat2-billiard.newbv.cn（无认证）
    - SnookerOmFetchWorker: 新球房运维管理后台接口 xqzg.newbv.cn（Session + CSRF 认证）
    - DevicesFetchWorker: 球房运维管理后台接口 kd.newbv.cn:30005（JWT Bearer Token 认证）
"""

import json
import os

import requests
from PySide6.QtCore import QThread, Signal

from core.app_paths import get_app_dir

# ==================== 配置读取 ====================

def _load_api_credentials():
    """从 settings.json 读取 API 账号密码配置"""
    settings_path = os.path.join(get_app_dir(), "settings.json")
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        settings = {}
    return settings.get("api_credentials", {})


def get_active_api_source() -> str:
    """读取当前启用的设备数据源（'kd' / 'xqzg'），默认 kd"""
    src = str(_load_api_credentials().get("active_source", "kd")).lower()
    return src if src in ("kd", "xqzg") else "kd"


# ==================== 原有接口（wechat2-billiard） ====================

BASE_URL = "https://wechat2-billiard.newbv.cn/prod-api/api/billiardtable/listext"
DEFAULT_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "AutoWork/1.0",
    "version": "1.4.0",
}


class TableFetchWorker(QThread):
    """异步拉取全量球桌数据（循环分页直至拉完，写入本地库）

    首页请求后读取接口返回的 total，若超过单页 pageSize 则自动翻页
    继续拉取，避免数据超过 1000 条时后续记录被静默遗漏。

    Signals:
        result_ready(list): 全量数据列表
        error(str): 错误信息
    """
    result_ready = Signal(list)
    error = Signal(str)

    # 单页大小与最大页数保护（防止接口异常导致死循环）
    _PAGE_SIZE = 1000
    _MAX_PAGES = 50

    # 除分页参数外的固定查询条件
    _FIXED_PARAMS = {
        "roomName": "", "roomInfo": "", "roomStatus": "",
        "calculated": "", "sales": "", "code": "",
        "name": "", "deviceVersion": "", "tableType": "",
        "contractStatus": "", "feeStatus": "",
        "startTime": "", "endTime": "",
        "billingStartDate": "", "billingEndDate": "",
        "contractEndStartDate": "", "contractEndEndDate": "",
        "startLastPlay": "", "endLastPlay": "",
        "status": "", "unlinedays": "", "nomatchdays": "",
        "onlineStatus": "",
    }

    def __init__(self, parent=None):
        super().__init__(parent)

    def _fetch_page(self, page_no: int) -> dict:
        """拉取单页数据，返回接口 data 字段（含 lists/total）"""
        params = {"pageNo": page_no, "pageSize": self._PAGE_SIZE}
        params.update(self._FIXED_PARAMS)
        resp = requests.get(BASE_URL, params=params,
                            headers=DEFAULT_HEADERS, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != 200:
            raise RuntimeError(payload.get('msg', '未知错误'))
        return payload.get("data") or {}

    def run(self):
        try:
            # 首页：拿到 total 与首批数据
            inner = self._fetch_page(1)
            rows = list(inner.get("lists") or [])
            total = inner.get("total")
            # total 可能为字符串，统一转 int；无法解析时按已拉取数量处理
            try:
                total = int(total)
            except (TypeError, ValueError):
                total = len(rows)

            # 循环翻页直至拉全
            page = 1
            while len(rows) < total and page < self._MAX_PAGES:
                page += 1
                inner = self._fetch_page(page)
                batch = inner.get("lists") or []
                if not batch:  # 接口提前返回空页，避免死循环
                    break
                rows.extend(batch)

            self.result_ready.emit(rows)
        except requests.exceptions.Timeout:
            self.error.emit("请求超时（20秒），请检查网络后重试")
        except requests.exceptions.ConnectionError:
            self.error.emit("网络连接失败，请检查网络")
        except Exception as e:
            self.error.emit(f"获取数据失败: {e}")


# ==================== 接口1: xqzg.newbv.cn（Session 认证） ====================

API1_BASE = "https://xqzg.newbv.cn"
API1_LOGIN_URL = f"{API1_BASE}/api/rbac/auth/login/"
API1_DATA_URL = f"{API1_BASE}/api/snooker_om/status/"


class SnookerOmFetchWorker(QThread):
    """异步拉取接口1数据（Session + CSRF 认证，支持过期自动重登录）

    Signals:
        result_ready(dict): 接口返回的完整 JSON（含 total, results, summary_row 等）
        error(str): 错误信息
    """
    result_ready = Signal(dict)
    error = Signal(str)

    def __init__(self, file_path="", page=1, pagesize=1000,
                 username=None, password=None, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.page = page
        self.pagesize = pagesize
        # 优先使用传入参数，否则从配置文件读取
        creds = _load_api_credentials()
        api1_cfg = creds.get("api1", {})
        self.username = username or api1_cfg.get("username", "")
        self.password = password or api1_cfg.get("password", "")

    def _login(self):
        """登录获取 session，返回 requests.Session 或 None"""
        session = requests.Session()
        try:
            resp = session.post(API1_LOGIN_URL, json={
                "username": self.username,
                "password": self.password,
            }, timeout=15)
            if resp.status_code == 200:
                return session
        except requests.exceptions.RequestException:
            pass
        return None

    def run(self):
        try:
            if not self.username or not self.password:
                self.error.emit("接口1账号密码未配置，请在 settings.json 的 api_credentials.api1 中填写")
                return

            session = self._login()
            if session is None:
                self.error.emit("接口1登录失败，请检查账号密码")
                return

            params = {
                "file_path": self.file_path,
                "page": self.page,
                "pagesize": self.pagesize,
            }
            resp = session.get(API1_DATA_URL, params=params, timeout=30)

            # Session 过期，自动重新登录重试一次
            if resp.status_code in (401, 403):
                session = self._login()
                if session is None:
                    self.error.emit("接口1 Session 过期且重新登录失败")
                    return
                resp = session.get(API1_DATA_URL, params=params, timeout=30)

            resp.raise_for_status()
            self.result_ready.emit(resp.json())

        except requests.exceptions.Timeout:
            self.error.emit("接口1请求超时（30秒），请检查网络")
        except requests.exceptions.ConnectionError:
            self.error.emit("接口1网络连接失败")
        except Exception as e:
            self.error.emit(f"接口1获取数据失败: {e}")


# ==================== 接口2: kd.newbv.cn:30005（JWT 认证） ====================

API2_BASE = "http://kd.newbv.cn:30005"
API2_LOGIN_URL = f"{API2_BASE}/api/getAccessToken/"
API2_DATA_URL = f"{API2_BASE}/api/devices/status/"
API2_MIGRATE_URL = f"{API2_BASE}/api/devices/migrate_image/"

# 分类目录映射：中文名 → 服务器目录名
CATEGORY_DIRS = {
    "正常": "normal",
    "操作": "except",
    "待处理": "pending",
    "使用": "operation",
    "精度": "accuracy",
    "问题": "already",
    "废弃": "rubbish",
}
# 反向映射：服务器目录名 → 中文名
DIR_CATEGORIES = {v: k for k, v in CATEGORY_DIRS.items()}


class DevicesFetchWorker(QThread):
    result_ready = Signal(dict)
    error = Signal(str)

    def __init__(self, file_path="", page=1, pagesize=1200, keyword="",
                 username=None, password=None, parent=None):
        super().__init__(parent)
        self.file_path = file_path
        self.page = page
        self.pagesize = pagesize
        # 搜索关键词：非空时拼入请求 URL，服务端只返回匹配设备
        self.keyword = str(keyword or "").strip()
        # 优先使用传入参数，否则从配置文件读取
        creds = _load_api_credentials()
        api2_cfg = creds.get("api2", {})
        self.username = username or api2_cfg.get("username", "")
        self.password = password or api2_cfg.get("password", "")
        self._token = None

    def _login(self):
        """登录获取 JWT access token，成功返回 token 字符串，失败返回 None"""
        try:
            resp = requests.post(API2_LOGIN_URL, json={
                "username": self.username,
                "password": self.password,
            }, timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                self._token = data.get("access")
                return self._token
        except requests.exceptions.RequestException:
            pass
        return None

    def run(self):
        try:
            if not self.username or not self.password:
                self.error.emit("接口2账号密码未配置，请在 settings.json 的 api_credentials.api2 中填写")
                return

            # 获取 token
            token = self._login()
            if not token:
                self.error.emit("接口2登录失败，请检查账号密码")
                return

            params = {
                "file_path": self.file_path,
                "page": self.page,
                "pagesize": self.pagesize,
            }
            # 搜索状态：携带 keyword 参数，服务端只返回匹配设备
            # （requests 的 params 字典自动做 URL 编码，中文等字符安全转义）
            if self.keyword:
                params["keyword"] = self.keyword
            headers = {"Authorization": f"Bearer {token}"}
            resp = requests.get(API2_DATA_URL, params=params,
                                headers=headers, timeout=30)

            # Token 过期 (401)，自动重新登录重试一次
            if resp.status_code == 401:
                token = self._login()
                if not token:
                    self.error.emit("接口2 Token 过期且重新登录失败")
                    return
                headers = {"Authorization": f"Bearer {token}"}
                resp = requests.get(API2_DATA_URL, params=params,
                                    headers=headers, timeout=30)

            resp.raise_for_status()
            self.result_ready.emit(resp.json())

        except requests.exceptions.Timeout:
            self.error.emit("接口2请求超时（30秒），请检查网络")
        except requests.exceptions.ConnectionError:
            self.error.emit("接口2网络连接失败")
        except Exception as e:
            self.error.emit(f"接口2获取数据失败: {e}")


# ==================== 图像迁移 Worker ====================

def build_image_path(file_path: str, device_code: str, category: str) -> str:
    """构造迁移路径: media/{日期}/{设备码}/{分类}/

    Args:
        file_path: 日期路径，如 "2026/08/02"
        device_code: 设备编码
        category: 中文分类名（正常/操作/待处理/使用/精度/问题/废弃）
    Returns:
        如 "media/2026/08/02/1M2WJ13CNPE1009A50167/except/"
    """
    dir_name = CATEGORY_DIRS.get(category, category)
    return f"media/{file_path}/{device_code}/{dir_name}/"


class MigrateImageWorker(QThread):
    """异步执行图像分类迁移（支持批量）

    Signals:
        success(int): 成功迁移的图片数量
        error(str): 错误信息
        progress(int, int): (当前进度, 总数)
    """
    success = Signal(int)
    error = Signal(str)
    progress = Signal(int, int)

    def __init__(self, file_path: str, device_code: str,
                 file_names: list, src_category: str, dest_category: str,
                 username=None, password=None, parent=None):
        """
        Args:
            file_path: 日期路径，如 "2026/08/02"
            device_code: 设备编码
            file_names: 要迁移的文件名列表
            src_category: 源分类（中文名，如 "操作"）
            dest_category: 目标分类（中文名，如 "待处理"）
        """
        super().__init__(parent)
        self.file_path = file_path
        self.device_code = device_code
        self.file_names = file_names
        self.src_category = src_category
        self.dest_category = dest_category
        creds = _load_api_credentials()
        api2_cfg = creds.get("api2", {})
        self.username = username or api2_cfg.get("username", "")
        self.password = password or api2_cfg.get("password", "")
        self._token = None

    def _login(self):
        """登录获取 JWT token"""
        try:
            resp = requests.post(API2_LOGIN_URL, json={
                "username": self.username,
                "password": self.password,
            }, timeout=15)
            if resp.status_code == 200:
                self._token = resp.json().get("access")
                return self._token
        except requests.exceptions.RequestException:
            pass
        return None

    def _migrate_one(self, file_name: str, src_path: str, dest_path: str) -> bool:
        """迁移单张图片，返回是否成功"""
        headers = {"Authorization": f"Bearer {self._token}"}
        form_data = {
            "src_path": (None, src_path),
            "dest_path": (None, dest_path),
            "file_name": (None, file_name),
        }
        resp = requests.post(API2_MIGRATE_URL, files=form_data,
                             headers=headers, timeout=20)

        # Token 过期重试一次
        if resp.status_code == 401:
            if not self._login():
                return False
            headers = {"Authorization": f"Bearer {self._token}"}
            resp = requests.post(API2_MIGRATE_URL, files=form_data,
                                 headers=headers, timeout=20)

        return resp.status_code == 200

    def run(self):
        try:
            if not self.username or not self.password:
                self.error.emit("接口2账号密码未配置")
                return

            if not self._login():
                self.error.emit("接口2登录失败，请检查账号密码")
                return

            src_path = build_image_path(self.file_path, self.device_code, self.src_category)
            dest_path = build_image_path(self.file_path, self.device_code, self.dest_category)

            total = len(self.file_names)
            ok_count = 0
            fail_list = []

            for i, fname in enumerate(self.file_names, 1):
                if self._migrate_one(fname, src_path, dest_path):
                    ok_count += 1
                else:
                    fail_list.append(fname)
                self.progress.emit(i, total)

            if fail_list:
                self.error.emit(
                    f"迁移完成：成功 {ok_count} 张，失败 {len(fail_list)} 张\n"
                    f"失败文件: {', '.join(fail_list[:5])}"
                    + ("..." if len(fail_list) > 5 else ""))
            else:
                self.success.emit(ok_count)

        except requests.exceptions.Timeout:
            self.error.emit("迁移请求超时，请检查网络")
        except requests.exceptions.ConnectionError:
            self.error.emit("网络连接失败")
        except Exception as e:
            self.error.emit(f"迁移失败: {e}")


# ==================== 登录测试（管理设置页「测试连接」用） ====================

class LoginTestWorker(QThread):
    """测试 API 登录是否成功

    Signals:
        success(str): 成功提示
        error(str): 失败原因
    """
    success = Signal(str)
    error = Signal(str)

    def __init__(self, api_name, username=None, password=None, parent=None):
        """
        Args:
            api_name: "api1"（xqzg）或 "api2"（kd）
        """
        super().__init__(parent)
        self.api_name = api_name
        creds = _load_api_credentials()
        cfg = creds.get(api_name, {})
        self.username = username or cfg.get("username", "")
        self.password = password or cfg.get("password", "")

    def run(self):
        if not self.username or not self.password:
            self.error.emit("账号或密码为空，请先填写")
            return
        try:
            if self.api_name == "api1":
                session = requests.Session()
                resp = session.post(API1_LOGIN_URL, json={
                    "username": self.username, "password": self.password}, timeout=15)
                ok = resp.status_code == 200
            else:
                resp = requests.post(API2_LOGIN_URL, json={
                    "username": self.username, "password": self.password}, timeout=15)
                ok = resp.status_code == 200 and bool(resp.json().get("access"))
            if ok:
                self.success.emit("账号密码验证通过")
            else:
                self.error.emit(f"登录失败（HTTP {resp.status_code}），请检查账号密码")
        except requests.exceptions.Timeout:
            self.error.emit("连接超时（15秒），请检查网络")
        except requests.exceptions.ConnectionError:
            self.error.emit("网络连接失败，请检查网络")
        except Exception as e:
            self.error.emit(f"测试失败: {e}")
