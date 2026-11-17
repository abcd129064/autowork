# -*- coding: utf-8 -*-
"""桌面端 /api/sync HTTP 传输层（步骤 3 客户端骨架，默认关闭）。

不替换既有 MySQL 直连链路：开关打开后由调用方按表逐步接入，未接入的
路径仍走 database/backend.py。用标准库 urllib，且显式绕开系统代理——
打本机隧道端口/自建服务时 HTTP_PROXY 会把请求带偏。
"""
import json
import urllib.error
import urllib.request

# 与服务端 sync_core.CONTRACT_VERSION 对齐；服务端升级语义时两处同步改，
# 客户端启动时先 check_contract()，版本不一致拒绝同步而不是猜字段。
CLIENT_CONTRACT_VERSION = "1.0.0-alpha.1"

try:
    from core import app_settings
except ImportError:  # 配置门面不可用时退化为「永不启用」，不拖垮导入方
    app_settings = None


class SyncTransportError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


def default_opener():
    """构造绕过 HTTP_PROXY 的 opener（打自建服务/本机隧道的必要条件）。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


class SyncTransport:
    """服务端 /api/sync 的最小调用封装；网络动作集中在 _request 便于 mock。"""

    def __init__(self, base_url: str, device_id: str, token: str,
                 timeout: float = 10.0, opener=None):
        self.base_url = str(base_url or "").rstrip("/")
        self.device_id = str(device_id or "")
        self.token = str(token or "")
        self.timeout = timeout
        self._opener = opener or default_opener()

    @classmethod
    def from_settings(cls):
        """读 sync_api 配置域构造实例；默认关闭，缺配置返回 None。"""
        if app_settings is None:
            return None
        try:
            cfg = app_settings.get_merged().get("sync_api") or {}
        except Exception:
            return None
        if not cfg.get("enabled"):
            return None
        if not (cfg.get("base_url") and cfg.get("device_id") and cfg.get("token")):
            return None
        return cls(cfg["base_url"], cfg["device_id"], cfg["token"],
                   timeout=float(cfg.get("timeout") or 10.0))

    def _request(self, method: str, path: str, payload=None) -> dict:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") \
            if payload is not None else None
        req = urllib.request.Request(
            self.base_url + path, data=data, method=method,
            headers={"X-Sync-Device": self.device_id,
                     "X-Sync-Token": self.token,
                     "Content-Type": "application/json"})
        try:
            with self._opener.open(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise SyncTransportError(f"HTTP {e.code}: {detail}", e.code) from e
        except urllib.error.URLError as e:
            raise SyncTransportError(f"network error: {e.reason}") from e
        try:
            return json.loads(body)
        except ValueError as e:
            raise SyncTransportError("invalid JSON response") from e

    def health(self) -> dict:
        return self._request("GET", "/api/sync/health")

    def get_contract(self) -> dict:
        return self._request("GET", "/api/sync/contract")

    def check_contract(self) -> dict:
        """同步前校验契约版本；不一致直接拒绝，避免按错字段写库。"""
        c = self.get_contract()
        if c.get("contract_version") != CLIENT_CONTRACT_VERSION:
            raise SyncTransportError(
                f"contract mismatch: server={c.get('contract_version')} "
                f"client={CLIENT_CONTRACT_VERSION}")
        return c

    def query(self, table: str, cursor: str = None, page_size: int = 200,
              active_only: bool = False) -> dict:
        return self._request("POST", f"/api/sync/{table}/query",
                             {"cursor": cursor, "page_size": page_size,
                              "active_only": active_only})

    def upsert(self, table: str, rows: list, dry_run: bool = False) -> dict:
        return self._request("POST", f"/api/sync/{table}/upsert",
                             {"rows": rows, "dry_run": dry_run})
