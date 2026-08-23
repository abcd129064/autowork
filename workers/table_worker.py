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
from core.secrets import decrypt_settings

# ==================== 配置读取 ====================

def _load_api_credentials():
    """从 settings.json 读取 API 账号密码配置（敏感字段透明解密）"""
    settings_path = os.path.join(get_app_dir(), "settings.json")
    try:
        with open(settings_path, "r", encoding="utf-8") as f:
            settings = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        settings = {}
    return decrypt_settings(settings).get("api_credentials", {})


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
        # 固定查询条件与分页参数合并：接口要求全量字段，缺省会改变返回结果
        params.update(self._FIXED_PARAMS)
        resp = requests.get(BASE_URL, params=params,
                            headers=DEFAULT_HEADERS, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("code") != 200:
            # 业务层错误码（非 HTTP 状态码）：接口约定 code=200 才算成功
            raise RuntimeError(payload.get('msg', '未知错误'))
        return payload.get("data") or {}

    def run(self):
        """首页拿 total 后循环翻页拉全（空页/超最大页数即停）"""
        try:
            # 首页：拿到 total 与首批数据（接口返回字段名为 count）
            inner = self._fetch_page(1)
            rows = list(inner.get("lists") or [])
            # 兼容两种字段命名：新接口返回 count，旧版可能叫 total
            total = inner.get("count") or inner.get("total")
            # total 可能为字符串，统一转 int；无法解析时按已拉取数量处理
            try:
                total = int(total)
            except (TypeError, ValueError):
                total = len(rows)

            # 循环翻页直至拉全（以「已请求页数 × 每页条数」是否覆盖 total 为准）
            page = 1
            while page * self._PAGE_SIZE < total and page < self._MAX_PAGES:
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
API1_MIGRATE_URL = f"{API1_BASE}/api/snooker_om/migrate_image/"


class SnookerOmFetchWorker(QThread):
    """异步拉取接口1数据（Session + CSRF 认证，支持过期自动重登录）

    Signals:
        result_ready(list): 拉取到的全量记录列表（自动按 total 翻页拉全，
            即接口 results 键的全部内容；单页 detail 数据不再透出）
        error(str): 错误信息
    """
    result_ready = Signal(list)
    error = Signal(str)

    # 单页大小与最大页数保护（防止接口异常导致死循环）
    _PAGE_SIZE = 1000
    _MAX_PAGES = 50

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
        """登录 → 拉数据，401/403 自动重登重试一次；按接口 total 循环翻页拉全

        首页拿 total 后自动翻页（与 TableFetchWorker 同策略），避免设备数
        超过单页 pagesize 时后续记录被静默遗漏。
        """
        try:
            if not self.username or not self.password:
                self.error.emit("接口1账号密码未配置，请在 settings.json 的 api_credentials.api1 中填写")
                return

            session = self._login()
            if session is None:
                self.error.emit("接口1登录失败，请检查账号密码")
                return

            base_params = {
                "file_path": self.file_path,
                "page": self.page,
                "pagesize": self.pagesize,
            }
            rows = []
            page = max(1, self.page)
            # 以「已请求页数 × 每页条数」是否覆盖 total 为准，翻页直至拉全
            while page - self.page < self._MAX_PAGES:
                params = dict(base_params, page=page)
                resp = session.get(API1_DATA_URL, params=params, timeout=30)

                # Session 过期，自动重新登录重试一次
                if resp.status_code in (401, 403):
                    session = self._login()
                    if session is None:
                        self.error.emit("接口1 Session 过期且重新登录失败")
                        return
                    resp = session.get(API1_DATA_URL, params=params, timeout=30)

                resp.raise_for_status()
                data = resp.json()
                batch = data.get("results") or []
                if not batch:  # 接口提前返回空页，避免死循环
                    break
                rows.extend(batch)
                # total 可能为字符串，统一转 int；无法解析时按已拉取数量处理
                try:
                    total = int(data.get("total") or 0)
                except (TypeError, ValueError):
                    total = len(rows)
                if page * int(self.pagesize) >= total:
                    break
                page += 1

            self.result_ready.emit(rows)

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
    """异步拉取接口2设备状态（JWT 认证，Token 过期自动重登，支持关键词搜索）

    Signals:
        result_ready(list): 拉取到的全量记录列表（自动按 total 翻页拉全，
            即接口 lists 键的全部内容）
        error(str): 错误信息
    """
    result_ready = Signal(list)
    error = Signal(str)

    # 单页大小与最大页数保护（防止接口异常导致死循环）
    _PAGE_SIZE = 1200
    _MAX_PAGES = 50

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
        """登录拿 token → 拉设备状态，401 自动重登重试一次；按 total 循环翻页拉全

        首页拿 total 后自动翻页，避免设备数超过单页 pagesize 时后续记录
        被静默遗漏（keyword 搜索时 total 为匹配数，同样翻页拉全）。
        """
        try:
            if not self.username or not self.password:
                self.error.emit("接口2账号密码未配置，请在 settings.json 的 api_credentials.api2 中填写")
                return

            # 获取 token
            token = self._login()
            if not token:
                self.error.emit("接口2登录失败，请检查账号密码")
                return

            base_params = {
                "file_path": self.file_path,
                "page": self.page,
                "pagesize": self.pagesize,
            }
            # 搜索状态：携带 keyword 参数，服务端只返回匹配设备
            # （requests 的 params 字典自动做 URL 编码，中文等字符安全转义）
            if self.keyword:
                base_params["keyword"] = self.keyword

            rows = []
            page = max(1, self.page)
            # 以「已请求页数 × 每页条数」是否覆盖 total 为准，翻页直至拉全
            while page - self.page < self._MAX_PAGES:
                params = dict(base_params, page=page)
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
                data = resp.json()
                batch = data.get("lists") or []
                if not batch:  # 接口提前返回空页，避免死循环
                    break
                rows.extend(batch)
                # total 可能为字符串，统一转 int；无法解析时按已拉取数量处理
                try:
                    total = int(data.get("total") or 0)
                except (TypeError, ValueError):
                    total = len(rows)
                if page * int(self.pagesize) >= total:
                    break
                page += 1

            self.result_ready.emit(rows)

        except requests.exceptions.Timeout:
            self.error.emit("接口2请求超时（30秒），请检查网络")
        except requests.exceptions.ConnectionError:
            self.error.emit("接口2网络连接失败")
        except Exception as e:
            self.error.emit(f"接口2获取数据失败: {e}")


# ==================== 图像迁移 Worker ====================

def build_image_path(file_path: str, device_code: str, category: str) -> str:
    """拼迁移路径 media/{日期}/{设备码}/{分类目录}/，分类中文按 CATEGORY_DIRS 映射"""
    dir_name = CATEGORY_DIRS.get(category, category)
    return f"media/{file_path}/{device_code}/{dir_name}/"


class MigrateImageWorker(QThread):
    """异步执行图像分类迁移，将图片从一个分类目录移动到另一个分类目录

    按数据源分派（source 参数）：
    - kd（接口2）：JWT 登录 + POST /api/devices/migrate_image/；
    - xqzg（接口1）：Session 登录 + POST /api/snooker_om/migrate_image/，
      需带 Referer + X-CSRFToken（Django CSRF 校验），Session 过期自动重登。
    成功判定：HTTP 200 且响应体 status 不为 "error"
    （xqzg 用 200 + {"status":"error","msg":...} 表达业务失败，只看状态码会假成功）。
    迁移完成后根据成功/失败数量分别触发 success 或 error 信号。

    Signals:
        success(int): 全部迁移成功时触发，参数为成功数量
        error(str): 存在失败或异常时触发，参数为错误描述
        progress(int, int): 每迁移一张触发，参数为(当前序号, 总数)
    """
    success = Signal(int)
    error = Signal(str)
    progress = Signal(int, int)

    def __init__(self, file_path: str, device_code: str,
                 file_names: list, src_category: str, dest_category: str,
                 username=None, password=None, parent=None, source="kd"):
        """file_path 形如 "2026/08/02"；分类传中文（如 "操作"→"待处理"）；
        source: "kd" / "xqzg"（决定端点与认证方式）；
        账号密码缺省时按 source 从 settings.json 对应 api1/api2 读取"""
        super().__init__(parent)
        self.file_path = file_path
        self.device_code = device_code
        self.file_names = file_names
        self.src_category = src_category
        self.dest_category = dest_category
        self.source = "xqzg" if source == "xqzg" else "kd"
        # 账号密码：优先用外部传入，兜底按源从 settings.json 配置读取
        creds = _load_api_credentials()
        cred_key = "api1" if self.source == "xqzg" else "api2"
        cred_cfg = creds.get(cred_key, {})
        self.username = username or cred_cfg.get("username", "")
        self.password = password or cred_cfg.get("password", "")
        self._token = None     # kd：JWT token，登录后赋值
        self._session = None   # xqzg：requests.Session，登录后赋值

    def _login(self):
        """按数据源登录：kd 返回 JWT token；xqzg 返回 Session；失败返回 None"""
        try:
            if self.source == "xqzg":
                session = requests.Session()
                resp = session.post(API1_LOGIN_URL, json={
                    "username": self.username,
                    "password": self.password,
                }, timeout=15)
                if resp.status_code == 200:
                    self._session = session
                    return session
                return None
            resp = requests.post(API2_LOGIN_URL, json={
                "username": self.username,
                "password": self.password,
            }, timeout=15)
            if resp.status_code == 200:
                # 接口返回格式: {"access": "eyJhbGciOi..."}
                self._token = resp.json().get("access")
                return self._token
        except requests.exceptions.RequestException:
            pass  # 网络异常静默处理，由调用方判断并提示
        return None

    def _post_migrate(self, file_name: str, src_path: str, dest_path: str):
        """按源发起一次迁移 POST，返回 (resp, status_code)；网络异常返回 (None, 0)"""
        # 接口要求 multipart/form-data 格式提交路径参数
        form_data = {
            "src_path": (None, src_path),
            "dest_path": (None, dest_path),
            "file_name": (None, file_name),
        }
        try:
            if self.source == "xqzg":
                # Django CSRF：HTTPS 下校验 Referer；POST 需带 X-CSRFToken
                headers = {
                    "Referer": f"{API1_BASE}/",
                    "X-CSRFToken":
                        (self._session.cookies.get("csrftoken") or "")
                        if self._session else "",
                }
                resp = self._session.post(API1_MIGRATE_URL, files=form_data,
                                          headers=headers, timeout=20)
            else:
                resp = requests.post(API2_MIGRATE_URL, files=form_data,
                                     headers={
                                         "Authorization":
                                             f"Bearer {self._token}"},
                                     timeout=20)
            return resp, resp.status_code
        except requests.exceptions.RequestException:
            return None, 0

    @staticmethod
    def _check_ok(resp) -> tuple:
        """成功判定：HTTP 200 且 body.status != 'error'。返回 (ok, msg)"""
        if resp is None:
            return False, "网络异常"
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}: {resp.text[:120]}"
        try:
            data = resp.json()
            if isinstance(data, dict) and data.get("status") == "error":
                return False, str(data.get("msg") or "业务失败")[:160]
        except ValueError:
            pass  # 非 JSON 响应按 HTTP 200 判定成功
        return True, ""

    def _migrate_one(self, file_name: str, src_path: str, dest_path: str) -> tuple:
        """迁移单张图片；认证过期（401/403，含 xqzg Session/CSRF 过期）
        自动重登重试一次。返回 (ok: bool, msg: str)"""
        resp, code = self._post_migrate(file_name, src_path, dest_path)
        if resp is not None and code in (401, 403):
            if self._login():
                resp, code = self._post_migrate(file_name, src_path, dest_path)
        return self._check_ok(resp)

    def run(self):
        """线程主入口：登录 → 构造路径 → 逐张迁移 → 汇总结果"""
        try:
            # 前置校验：账号密码必须已配置（按源提示对应接口）
            src_name = "接口1(xqzg)" if self.source == "xqzg" else "接口2(kd)"
            if not self.username or not self.password:
                self.error.emit(f"{src_name}账号密码未配置")
                return

            # 第一步：登录（kd 拿 JWT / xqzg 拿 Session）
            if not self._login():
                self.error.emit(f"{src_name}登录失败，请检查账号密码")
                return

            # 第二步：拼接源路径和目标路径（media/{日期}/{设备码}/{分类}/）
            src_path = build_image_path(self.file_path, self.device_code, self.src_category)
            dest_path = build_image_path(self.file_path, self.device_code, self.dest_category)

            # 第三步：逐张迁移并实时更新进度
            total = len(self.file_names)
            ok_count = 0
            fail_list = []  # 记录失败文件名，方便排查

            for i, fname in enumerate(self.file_names, 1):
                ok, why = self._migrate_one(fname, src_path, dest_path)
                if ok:
                    ok_count += 1
                else:
                    fail_list.append(f"{fname}（{why}）" if why else fname)
                self.progress.emit(i, total)  # 通知 UI 刷新进度条

            # 第四步：汇总结果，全部成功走 success，有失败走 error
            if fail_list:
                shown = ', '.join(fail_list[:5])
                suffix = f'（另有 {len(fail_list) - 5} 个文件未列出）' if len(fail_list) > 5 else ''
                self.error.emit(
                    f"迁移完成：成功 {ok_count} 张，失败 {len(fail_list)} 张\n"
                    f"失败文件: {shown}{suffix}")
            else:
                self.success.emit(ok_count)

        except requests.exceptions.Timeout:
            self.error.emit("迁移请求超时，请检查网络")
        except requests.exceptions.ConnectionError:
            self.error.emit("网络连接失败")
        except Exception as e:
            self.error.emit(f"迁移失败: {e}")


# ==================== 健康度重置（xqzg update_health） ====================

API1_UPDATE_HEALTH_URL = f"{API1_BASE}/api/snooker_om/update_health/"


class HealthUpdateWorker(QThread):
    """异步重置设备健康度：POST xqzg /api/snooker_om/update_health/

    逐台把服务端健康度写为 4000（接口默认值，等于「清零」告警）；
    Session + CSRF 认证（与 MigrateImageWorker 的 xqzg 路径一致），
    401/403 自动重登重试一次。

    成功判定：HTTP 200 且响应体 code == 200（业务成功，响应形如
    {"code": 200, "msg": "成功", "data": {...}}）；只看状态码会假成功。

    Signals:
        result_ready(list, list): (成功球桌名列表, 失败列表
            [(球桌名, 失败描述), ...])
        error(str): 账号未配置 / 登录失败等整体错误
    """
    result_ready = Signal(list, list)
    error = Signal(str)

    def __init__(self, pairs, parent=None):
        """pairs: [(name, device_code), ...]，name 为球桌号仅用于失败提示"""
        super().__init__(parent)
        self.pairs = [(str(n or "").strip(), str(c or "").strip())
                      for n, c in (pairs or [])]
        creds = _load_api_credentials()
        api1_cfg = creds.get("api1", {})
        self.username = api1_cfg.get("username", "")
        self.password = api1_cfg.get("password", "")

    def _login(self):
        """登录获取 session（与 SnookerOmFetchWorker 同款），失败返回 None"""
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

    def _post_update(self, session, device_code) -> tuple:
        """单台 POST update_health，返回 (ok, err)；err='SESSION' 表示需重登"""
        # Django CSRF：HTTPS 下校验 Referer；POST 需带 X-CSRFToken
        headers = {
            "Referer": f"{API1_BASE}/",
            "X-CSRFToken": session.cookies.get("csrftoken") or "",
        }
        try:
            resp = session.post(API1_UPDATE_HEALTH_URL,
                                json={"device_code": device_code, "health": 4000},
                                headers=headers, timeout=20)
            if resp.status_code in (401, 403):
                return False, "SESSION"
            if resp.status_code != 200:
                return False, f"HTTP {resp.status_code}"
            data = resp.json()
            if isinstance(data, dict) and data.get("code") == 200:
                return True, ""
            return False, str(data.get("msg") or "接口返回失败")[:160]
        except requests.exceptions.Timeout:
            return False, "请求超时"
        except requests.exceptions.RequestException:
            return False, "网络连接失败"
        except ValueError:
            return False, "响应解析失败"

    def run(self):
        """登录 → 逐台重置 → 汇总 (成功名单, 失败列表)；无设备码直接计失败"""
        try:
            if not self.username or not self.password:
                self.error.emit("接口1账号密码未配置，请在 settings.json 的 api_credentials.api1 中填写")
                return
            session = self._login()
            if session is None:
                self.error.emit("接口1登录失败，请检查账号密码")
                return
            ok_names, fails = [], []
            for name, code in self.pairs:
                if not code:
                    fails.append((name, "无设备码（球桌库未匹配到设备）"))
                    continue
                ok, err = self._post_update(session, code)
                if err == "SESSION":
                    # Session 过期：重登后重试一次；重登失败保留旧 session
                    # 继续后续设备（勿把 session 置 None，否则后续全崩）
                    new_session = self._login()
                    if new_session is None:
                        fails.append((name, "会话过期且重新登录失败"))
                        continue
                    session = new_session
                    ok, err = self._post_update(session, code)
                if ok:
                    ok_names.append(name)
                else:
                    fails.append((name, err or "未知错误"))
            self.result_ready.emit(ok_names, fails)
        except Exception as e:
            self.error.emit(f"重置健康度失败: {e}")


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
        """api_name: "api1"（xqzg Session）或 "api2"（kd JWT）"""
        super().__init__(parent)
        self.api_name = api_name
        creds = _load_api_credentials()
        cfg = creds.get(api_name, {})
        self.username = username or cfg.get("username", "")
        self.password = password or cfg.get("password", "")

    def run(self):
        """按 api_name 走对应登录接口，仅验证认证是否通过"""
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
