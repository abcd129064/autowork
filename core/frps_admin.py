# -*- coding: utf-8 -*-
"""frps admin API 感知客户端（frps 0.65 v1 端点，只读）——二期 P0

数据源：``GET {base_url}/api/proxy/xtcp`` —— frps 0.65 起即有（源码核验
v0.65.0 server/dashboard_api.go），每条 xtcp proxy 自带
``status(online|offline) / curConns / lastStartTime / todayTrafficIn/Out``。
现场设备 frpc 注册 xtcp proxy 且控制连接存活 ⇔ status=online，这是
「设备是否在线」的权威判据（替代不可信的设备侧主动上报链路）。

frps 网页面板（Proxies 页 TCP/UDP/HTTP/…清单）同源 API（2026-09-23 源码
核验 frp-dev/server/api_router.go，v1 端点 0.65 即有）：
``GET /api/proxy/{type}``（type ∈ tcp/udp/http/https/tcpmux/stcp/sudp/xtcp）
→ {"proxies":[{name,conf,user,clientID,todayTrafficIn/Out,curConns,
lastStartTime,lastCloseTime,status}]}；网页「Traffic」按钮走
``GET /api/traffic/{name}``；客户端版本走 ``GET /api/clients``
（clientID → version，proxy.clientID 关联）。本模块经 all_proxies() 暴露
全类型清单（best-effort，失败不影响 xtcp 权威判据）。

状态口径（online() / preflight 共用）：
  "online"        —— 名单内且 status=online：放行建会话
  "offline"       —— 名单内但 status=offline：设备掉线
  "unregistered"  —— 名单内无此 snk：设备从未注册（未上线/已注销）
  None            —— 感知不可用（未配置/不可达/凭据错/缓存过期/熔断静默中）
                     调用方必须按「未知」处理回退一期纯本地行为，绝不阻塞连接

设计约束：
- 纯查询零风险：本模块只发 GET，不触碰任何 frps 写端点；
- 失败静默：连续 3 次失败熔断 60s（只停请求，不清缓存——缓存超过
  ttl 后 online() 返回 None，避免拿旧数据误判在线）；
- 口令走配置门面 credentials 域（DPAPI 加密落盘），本模块只在内存持有。
"""
from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.request

from PySide6.QtCore import QObject, QTimer, Signal

# frps 0.65 v1：xtcp proxy 列表端点（感知唯一数据源，批量一次拿全）
_PROXY_ENDPOINT = "/api/proxy/xtcp"
# frps 0.65 v1：服务端概览端点（版本/在线客户端/当前连接/今日总流量/
# 各类型代理数）——v0.65.0 server/dashboard_api.go apiServerInfo
_SERVERINFO_ENDPOINT = "/api/serverinfo"
# frps 网页面板 Proxies 页同源端点（v1，0.65 即有，server/api_router.go）：
# 全类型代理清单 / 客户端清单（clientID → frpc 版本，面板 ClientVersion 列）
_PROXY_TYPES = ("tcp", "udp", "http", "https", "tcpmux", "stcp", "sudp", "xtcp")
_CLIENTS_ENDPOINT = "/api/clients"
_DEFAULT_TIMEOUT_SEC = 2.5
_CIRCUIT_FAILS = 3          # 连续失败 N 次进入熔断
_CIRCUIT_COOLDOWN_SEC = 60  # 熔断静默时长
_CACHE_TTL_SEC = 90         # 缓存最长可信期（2× 默认刷新间隔+余量），超期按未知


def _http_get(url: str, user: str, password: str,
              timeout: float) -> tuple:
    """GET + BasicAuth，返回 (HTTP状态码或0=不可达, body 文本)

    模块级函数：单测直接 monkeypatch，不碰真实网络。
    强制直连：frps 为自建服务端（公网/内网），本机 HTTP_PROXY 调试代理
    对其端口不可达且会伪造 502，ProxyHandler({}) 绕过一切代理。
    """
    req = urllib.request.Request(url, method="GET")
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(2_000_000).decode(
                "utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        try:
            body = e.read(400).decode("utf-8", errors="replace")
        except Exception:
            body = ""
        return e.code, body
    except (urllib.error.URLError, OSError, ValueError):
        return 0, ""


def _parse_proxies(body: str) -> dict | None:
    """解析 /api/proxy/xtcp 响应 → {proxyName: info}；结构异常返回 None

    0.65 响应：{"proxies":[{"name":…,"status":"online|offline","curConns":n,
    "lastStartTime":…,"todayTrafficIn":n,"todayTrafficOut":n, …}]}
    """
    try:
        data = json.loads(body)
    except ValueError:
        return None
    items = data.get("proxies") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    result = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        conf = it.get("conf") if isinstance(it.get("conf"), dict) else {}
        result[name] = {
            "status": str(it.get("status") or "").lower(),
            "curConns": int(it.get("curConns") or 0),
            "lastStartTime": str(it.get("lastStartTime") or ""),
            "todayTrafficIn": int(it.get("todayTrafficIn") or 0),
            "todayTrafficOut": int(it.get("todayTrafficOut") or 0),
            # 2026-09-23 全类型清单复用：v1 响应本含 conf/clientID/user
            # （model/types.go ProxyStatsInfo），一并保留供「frps 代理」
            # 视图 xtcp 页签展示端口/版本列（不额外发 GET）
            "user": str(it.get("user") or ""),
            "clientID": str(it.get("clientID") or ""),
            "conf": conf,
        }
    return result


def _parse_serverinfo(body: str) -> dict | None:
    """解析 /api/serverinfo 响应 → 概览 dict；结构异常返回 None

    0.65 字段（v0.65.0 dashboard_api.go serverInfoResp）：version、
    totalTrafficIn/Out、curConns、clientCounts、proxyTypeCounts 等。
    """
    try:
        data = json.loads(body)
    except ValueError:
        return None
    if not isinstance(data, dict) or "version" not in data:
        return None

    def _i(key):
        try:
            return int(data.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    counts = data.get("proxyTypeCounts")
    return {
        "version": str(data.get("version") or ""),
        "bindPort": _i("bindPort"),
        "curConns": _i("curConns"),
        "clientCounts": _i("clientCounts"),
        "totalTrafficIn": _i("totalTrafficIn"),
        "totalTrafficOut": _i("totalTrafficOut"),
        "proxyTypeCounts": counts if isinstance(counts, dict) else {},
    }


def _parse_proxy_list(body: str) -> list | None:
    """解析 GET /api/proxy/{type} 响应 → list[dict]；结构异常返回 None

    0.65 响应：{"proxies":[{name,conf,user,clientID,todayTrafficIn,
    todayTrafficOut,curConns,lastStartTime,lastCloseTime,status}]}
    （model/types.go ProxyStatsInfo）。conf 为完整代理配置（含 remotePort/
    localPort 等），原样保留供 UI 展示端口列。
    """
    try:
        data = json.loads(body)
    except ValueError:
        return None
    items = data.get("proxies") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    result = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        if not name:
            continue
        conf = it.get("conf") if isinstance(it.get("conf"), dict) else {}
        result.append({
            "name": name,
            "user": str(it.get("user") or ""),
            "clientID": str(it.get("clientID") or ""),
            "status": str(it.get("status") or "").lower(),
            "curConns": int(it.get("curConns") or 0),
            "lastStartTime": str(it.get("lastStartTime") or ""),
            "todayTrafficIn": int(it.get("todayTrafficIn") or 0),
            "todayTrafficOut": int(it.get("todayTrafficOut") or 0),
            "conf": conf,
        })
    return result


def _parse_clients(body: str) -> dict | None:
    """解析 GET /api/clients 响应 → {clientID: version}；结构异常返回 None

    0.65 响应：{"clients":[{key,user,clientID,runID,version,hostname,
    online,…}]}（model/types.go ClientInfoResp）。网页面板 proxy 行的
    ClientVersion 列即由此经 clientID 关联而来。
    """
    try:
        data = json.loads(body)
    except ValueError:
        return None
    items = data.get("clients") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    result = {}
    for it in items:
        if not isinstance(it, dict):
            continue
        cid = str(it.get("clientID") or "").strip()
        if cid:
            result[cid] = str(it.get("version") or "")
    return result


def _load_config() -> dict:
    """读 frps_admin 配置（credentials 域，门面已透明解密）"""
    try:
        from core import app_settings
        cfg = app_settings.get("frps_admin") or {}
    except Exception:
        cfg = {}
    return cfg if isinstance(cfg, dict) else {}


def _load_quality() -> dict:
    try:
        from core import app_settings
        q = app_settings.get("frp_quality") or {}
    except Exception:
        q = {}
    q = q if isinstance(q, dict) else {}
    try:
        interval = int(q.get("interval_sec") or 30)
    except (TypeError, ValueError):
        interval = 30
    return {
        "enabled": bool(q.get("enabled", True)),
        "interval_sec": max(5, min(600, interval)),
    }


class FrpsAdminClient(QObject):
    """frps xtcp proxy 名单感知客户端（进程级单例，主线程 QTimer 刷新）"""

    proxies_changed = Signal(dict)       # {name: info} 刷新成功
    serverinfo_changed = Signal(object)  # dict|None 概览（/api/serverinfo）
    all_proxies_changed = Signal(dict)   # {type: [proxy,…]} 全类型清单（网页面板同源）
    channel_state_changed = Signal(str)  # ok|unreachable|unauthorized|error|unconfigured
    refresh_finished = Signal(str)       # request_refresh 完成回执（worker 线程
                                         # emit → queued 投递主线程，供 UI 一次性反馈）

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()    # 仅保护 _proxies/_ts 读写（HTTP 在锁外）
        self._proxies: dict = {}
        self._serverinfo: dict | None = None  # /api/serverinfo 概览（best-effort）
        self._all_proxies: dict = {}     # {type: [proxy,…]} 网页面板同源清单
        self._clients: dict = {}         # {clientID: version}（ClientVersion 列）
        self._fetched_at: float = 0.0    # 最近一次成功拉取（time.monotonic）
        self._state = "unconfigured"
        self._fails = 0
        self._circuit_until = 0.0        # 熔断静默截止（monotonic）
        self._refreshing = False         # 防同步 refresh 重入
        self._timer = QTimer(self)
        self._timer.setInterval(_load_quality()["interval_sec"] * 1000)
        self._timer.timeout.connect(self.request_refresh)
        self._running = False
        self._bg_busy = False           # 后台感知线程占用标记（防叠加）

    # ---------- 生命周期 ----------

    def start(self):
        """启动周期感知（配置缺失/关闭时不报错，仅状态为 unconfigured）"""
        if self._running:
            return
        cfg = _load_config()
        quality = _load_quality()
        if not cfg.get("base_url") or not quality["enabled"]:
            self._set_state("unconfigured")
            return
        self._running = True
        self._timer.setInterval(quality["interval_sec"] * 1000)
        self._timer.start()
        # 页面构造即触发 start（RemoteHub.__init__）：首轮感知也走后台线程，
        # 绝不在 GUI 线程同步 GET（frps 不可达时最坏挂 2.5s 拖住建页）
        self.request_refresh()

    def stop(self):
        self._running = False
        self._timer.stop()

    def restart_timer(self):
        """配置面板改 URL/凭据/间隔后热生效（重读配置并立即感知一次）"""
        self._timer.stop()
        self._running = False
        with self._lock:
            self._proxies = {}
            self._serverinfo = None
            self._all_proxies = {}
            self._clients = {}
            self._fetched_at = 0.0
        self._fails = 0
        self._circuit_until = 0.0
        self.start()   # start 内部已含 request_refresh（首轮感知走后台线程）

    # ---------- 感知 ----------

    def request_refresh(self):
        """后台线程执行 refresh：定时器周期、UI「立即感知/测试连接」统一入口

        周期感知绝不能在 GUI 线程同步 GET（frps 半开连接可挂满超时）；
        完成/失败状态经 refresh_finished 信号 queued 回主线程，UI 据此
        给一次性回执（2026-09-22 反馈：改异步后按钮"看起来没反应"——
        感知成功但数据无变化时缺完成提示）。
        """
        if self._bg_busy:
            return
        self._bg_busy = True

        def _job():
            try:
                state = self.refresh()
            except Exception:
                state = self._state
            finally:
                self._bg_busy = False
            self.refresh_finished.emit(state)
        threading.Thread(target=_job, daemon=True,
                         name="frps-admin-refresh").start()

    def refresh(self) -> str:
        """拉取 xtcp proxy 名单；返回通道状态。

        熔断静默期内直接返回当前状态不发请求（防重试风暴刷屏日志）；
        缓存是否可信由调用方经 _fresh() 判定，过期自动降级为不可用。
        """
        cfg = _load_config()
        base_url = str(cfg.get("base_url") or "").strip()
        if not base_url:
            self._set_state("unconfigured")
            return self._state
        now = time.monotonic()
        if now < self._circuit_until:
            return self._state
        if self._refreshing:
            return self._state
        self._refreshing = True
        try:
            url = base_url.rstrip("/") + _PROXY_ENDPOINT
            status, body = _http_get(
                url, str(cfg.get("user") or ""),
                str(cfg.get("password") or ""), _DEFAULT_TIMEOUT_SEC)
        finally:
            self._refreshing = False
        if status == 200:
            parsed = _parse_proxies(body)
            if parsed is None:
                # 200 但结构异常（如 SPA 回退页/代理劫持）：按失败计，
                # 且把响应前缀记入日志帮助定位（内容类型守卫同更新器思路）
                self._on_fail("响应结构异常")
                return self._state
            with self._lock:
                self._proxies = parsed
                self._fetched_at = time.monotonic()
            self._fails = 0
            self._set_state("ok")
            # 概览数据 best-effort：失败只清 _serverinfo（概览卡显示—），
            # 不计入熔断、不影响 proxies 权威判据与 ok 状态
            self._refresh_serverinfo(cfg)
            # 全类型代理清单（frps 网页面板 Proxies 页同源）best-effort：
            # 失败只清 _all_proxies（面板视图显示空表），同概览纪律不降级感知
            self._refresh_all_proxies(cfg, parsed)
            self.proxies_changed.emit(dict(parsed))
            return self._state
        if status in (401, 403):
            self._on_fail("凭据被拒", state="unauthorized")
        elif status == 0:
            self._on_fail("不可达")
        else:
            self._on_fail(f"HTTP {status}")
        return self._state

    def _on_fail(self, why: str, state: str = "unreachable"):
        self._fails += 1
        if self._fails >= _CIRCUIT_FAILS:
            self._fails = 0
            self._circuit_until = time.monotonic() + _CIRCUIT_COOLDOWN_SEC
        self._set_state(state)

    def _refresh_serverinfo(self, cfg: dict):
        """GET /api/serverinfo（与 proxies 同线程同凭据，best-effort）

        概览卡数据源。任何失败（不可达/凭据/结构异常/非 200）只把
        _serverinfo 置 None 并发 serverinfo_changed(None)——绝不改动
        通道状态、不计熔断：在线感知权威判据是 /api/proxy/xtcp，
        旧版 frps 未含该端点时页面也只是概览卡变「—」。
        """
        info = None
        try:
            base_url = str(cfg.get("base_url") or "").strip()
            if base_url:
                s, b = _http_get(
                    base_url.rstrip("/") + _SERVERINFO_ENDPOINT,
                    str(cfg.get("user") or ""),
                    str(cfg.get("password") or ""),
                    _DEFAULT_TIMEOUT_SEC)
                if s == 200:
                    info = _parse_serverinfo(b)
        except Exception:
            info = None
        with self._lock:
            self._serverinfo = info
        self.serverinfo_changed.emit(info)

    def _refresh_all_proxies(self, cfg: dict, xtcp_parsed: dict):
        """GET /api/proxy/{type} ×7 + /api/clients（网页面板 Proxies 页同源）

        xtcp 不重复请求：权威名单（/api/proxy/xtcp）本轮已拉，直接复用其
        解析结果填入 result["xtcp"]（无 conf/clientID，端口/版本列显「—」）。
        best-effort：任一类型失败即跳过该类型（空列表），clients 失败则
        版本列显示「—」；全程不改通道状态、不计熔断——与概览同纪律。
        7 次串行 GET 各 2.5s 超时上限，最坏 ~18s，但只在后台线程执行。
        """
        user = str(cfg.get("user") or "")
        password = str(cfg.get("password") or "")
        base_url = str(cfg.get("base_url") or "").strip().rstrip("/")
        result: dict = {}
        if base_url:
            for ptype in _PROXY_TYPES:
                if ptype == "xtcp":
                    continue  # 权威名单已含，复用不重拉
                try:
                    s, b = _http_get(
                        f"{base_url}/api/proxy/{ptype}", user, password,
                        _DEFAULT_TIMEOUT_SEC)
                    if s == 200:
                        parsed = _parse_proxy_list(b)
                        if parsed is not None:
                            result[ptype] = parsed
                except Exception:
                    continue
        # _parse_proxies 已保留 conf/clientID/user，直接复用为清单行
        result["xtcp"] = [
            dict(i, name=n) for n, i in (xtcp_parsed or {}).items()]
        clients: dict = {}
        if base_url and result:
            try:
                s, b = _http_get(base_url + _CLIENTS_ENDPOINT, user, password,
                                 _DEFAULT_TIMEOUT_SEC)
                if s == 200:
                    clients = _parse_clients(b) or {}
            except Exception:
                clients = {}
        with self._lock:
            self._all_proxies = result
            self._clients = clients
        self.all_proxies_changed.emit(dict(result))

    def _set_state(self, state: str):
        if state != self._state:
            self._state = state
            self.channel_state_changed.emit(state)

    def _fresh(self) -> bool:
        with self._lock:
            return (self._fetched_at > 0
                    and time.monotonic() - self._fetched_at < _CACHE_TTL_SEC)

    # ---------- 查询 ----------

    def _lookup(self, snk: str) -> dict | None:
        """按 snk 查 proxy：精确名 → user 前缀名（"{user}.{snk}"）兜底"""
        snk = str(snk or "").strip()
        if not snk:
            return None
        with self._lock:
            proxies = self._proxies
        info = proxies.get(snk)
        if info is None:
            suffix = "." + snk
            for name, it in proxies.items():
                if name.endswith(suffix):
                    info = it
                    break
        return info

    def online(self, snk: str) -> str | None:
        """感知 snk 在线态：online/offline/unregistered；感知不可用返回 None"""
        if not str(snk or "").strip():
            return None
        if self._state != "ok" or not self._fresh():
            return None
        info = self._lookup(snk)
        if info is None:
            return "unregistered"
        return "online" if info["status"] == "online" else "offline"

    def info(self, snk: str) -> dict | None:
        """proxy 明细（curConns/lastStartTime/流量），未感知到返回 None"""
        if self._state != "ok" or not self._fresh():
            return None
        return self._lookup(snk)

    def serverinfo(self) -> dict | None:
        """frps 概览（/api/serverinfo）；未拉到/缓存过期返回 None"""
        if self._state != "ok" or not self._fresh():
            return None
        with self._lock:
            return self._serverinfo

    def all_proxies(self) -> dict:
        """全类型代理清单 {type: [proxy,…]}（frps 网页面板 Proxies 页同源）

        proxy 字段：name/user/clientID/status/curConns/lastStartTime/
        todayTrafficIn/Out/conf。未拉到/缓存过期返回空 dict（UI 显空表）。
        """
        if self._state != "ok" or not self._fresh():
            return {}
        with self._lock:
            return {k: list(v) for k, v in self._all_proxies.items()}

    def client_version(self, client_id: str) -> str:
        """proxy.clientID → frpc 版本（网页面板 ClientVersion 列同源）"""
        cid = str(client_id or "").strip()
        if not cid or self._state != "ok" or not self._fresh():
            return ""
        with self._lock:
            return self._clients.get(cid, "")

    def snapshot(self) -> dict:
        """UI 一次性快照（总览表/球桌页富集用）"""
        with self._lock:
            proxies = dict(self._proxies)
            serverinfo = self._serverinfo
        return {
            "state": self._state,
            "fresh": self._fresh(),
            "age_sec": (round(time.monotonic() - self._fetched_at, 1)
                        if self._fetched_at else None),
            "proxies": proxies,
            "serverinfo": serverinfo,
        }

    def configured(self) -> bool:
        return bool(str(_load_config().get("base_url") or "").strip())


_instance: FrpsAdminClient | None = None


def get_frps_client() -> FrpsAdminClient:
    """进程级单例（懒建；首次获取不自动 start，由 UI 初始化显式启动）"""
    global _instance
    if _instance is None:
        _instance = FrpsAdminClient()
    return _instance
