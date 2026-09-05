# -*- coding: utf-8 -*-
"""本地售后面板 Web 服务：桌面程序运行时顺带开一个本地端口，浏览器即可访问售后面板网页。

- 静态资源：托管售后面板 Web 前端构建产物（web/aftersale_front/dist，
  打包环境位于 _internal/web_dist/，开发环境直接读仓库内 dist/）
- /api/*：反向代理到云端售后面板 API（与线上站点同一数据源，接口/口径完全一致）
- 纯标准库实现（http.server + urllib），零新增依赖；daemon 线程，绝不阻塞/影响 GUI

settings.json 可选配置（缺省用默认值）：
    "local_web": { "enabled": true, "port": 8787, "api_base": "http://49.235.34.253" }

独立测试（不经 GUI）：
    python -m core.local_web_server --port 8787
"""

import os
import sys
import json
import socket
import threading
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.app_paths import get_app_dir, get_resource_dir

DEFAULT_PORT = 8787
DEFAULT_API_BASE = "http://49.235.34.253"
_API_TIMEOUT = 10  # 反代上游超时（秒）

_MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".map": "application/json",
}

_server = None  # 模块级单例，避免重复启动


def _resolve_dist_dir():
    """前端构建产物目录探测：环境变量覆盖 → 打包资源 → 开发仓库 dist → app_dir/web_dist"""
    candidates = []
    _ov = os.environ.get("LOCALWEB_DIST_OVERRIDE")
    if _ov:
        candidates.append(_ov)
    candidates += [
        os.path.join(get_resource_dir(), "web_dist"),
        os.path.join(get_app_dir(), "web", "aftersale_front", "dist"),
        os.path.join(get_app_dir(), "web_dist"),
    ]
    for d in candidates:
        if os.path.isfile(os.path.join(d, "index.html")):
            return d
    return None


def _log(level, msg):
    """日志：GUI 环境走 conn_logger；独立运行时退化为 print。永不抛异常。"""
    try:
        from core.conn_logger import conn_logger
        getattr(conn_logger, level if level != "warn" else "error")("LOCALWEB", msg)
    except Exception:
        try:
            print(f"[local_web] {level}: {msg}")
        except Exception:
            pass


class _Handler(BaseHTTPRequestHandler):
    server_version = "AutoWorkLocalWeb/1.0"
    protocol_version = "HTTP/1.1"

    # 静态根目录（启动时注入到 server 实例）
    @property
    def dist_dir(self):
        return self.server.dist_dir

    @property
    def api_base(self):
        return self.server.api_base

    def log_message(self, fmt, *args):  # 静默默认访问日志（避免刷 conn 日志）
        pass

    def _send(self, code, body: bytes, ctype: str, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    # ---- 静态文件 ----
    def _serve_static(self, path: str):
        rel = path.lstrip("/") or "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        full = os.path.normpath(os.path.join(self.dist_dir, rel))
        # 防路径穿越（含 %2e%2e 等 URL 编码变体：解码后复检）
        root = os.path.normpath(self.dist_dir)
        if not full.startswith(root):
            return self._send_json(403, {"error": "forbidden"})
        from urllib.parse import unquote
        dec = os.path.normpath(os.path.join(self.dist_dir, unquote(rel).lstrip("/")))
        if not dec.startswith(root):
            return self._send_json(403, {"error": "forbidden"})
        if not os.path.isfile(full):
            # SPA 兜底：未知路径回退 index.html（当前为单页，保险起见）
            full = os.path.join(self.dist_dir, "index.html")
            if not os.path.isfile(full):
                return self._send_json(404, {"error": "web_dist missing (build frontend first)"})
        ext = os.path.splitext(full)[1].lower()
        ctype = _MIME.get(ext, "application/octet-stream")
        with open(full, "rb") as f:
            body = f.read()
        # 带 hash 的 assets 可长缓存；index.html 不缓存
        cache = "public, max-age=31536000, immutable" if "/assets/" in rel.replace("\\", "/") else "no-cache"
        self._send(200, body, ctype, {"Cache-Control": cache})

    # ---- API 反代 ----
    def _proxy(self):
        qs = self.path.partition("?")[2]
        url = f"{self.api_base}{self.path}"  # self.path 已含 /api/... 与 query
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(url, data=body, method=self.command)
        ct = self.headers.get("Content-Type")
        if ct:
            req.add_header("Content-Type", ct)
        try:
            with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
                data = resp.read()
                self._send(resp.status, data, resp.headers.get("Content-Type", "application/json"))
        except urllib.error.HTTPError as e:
            self._send(e.code, e.read(), e.headers.get("Content-Type", "application/json"))
        except Exception as e:
            self._send_json(502, {"error": f"upstream unreachable: {e}"})

    def do_GET(self):
        if self.path.startswith("/api/"):
            self._proxy()
        else:
            self._serve_static(self.path)

    def do_POST(self):
        self._proxy()

    def do_PUT(self):
        self._proxy()

    def do_DELETE(self):
        self._proxy()


def start_local_web_server(settings: dict = None):
    """按 settings['local_web'] 启动本地 Web 服务（幂等；失败仅记日志，不影响 GUI）。

    返回: {"enabled": bool, "started": bool, "port": int, "url": str, "reason": str}"""
    global _server
    cfg = (settings or {}).get("local_web") or {}
    enabled = bool(cfg.get("enabled", True))
    port = int(cfg.get("port") or DEFAULT_PORT)
    api_base = str(cfg.get("api_base") or DEFAULT_API_BASE).rstrip("/")
    info = {"enabled": enabled, "started": False, "port": port,
            "url": f"http://localhost:{port}", "reason": ""}

    if not enabled:
        info["reason"] = "disabled by settings"
        return info
    if _server is not None:
        info["started"] = True
        info["reason"] = "already running"
        return info

    dist_dir = _resolve_dist_dir()
    if not dist_dir:
        info["reason"] = "web_dist not found (frontend not built)"
        _log("error", f"本地售后面板未启动：未找到前端构建产物（{info['reason']}）")
        return info

    # 端口占用预检（占用则静默跳过，不干扰 GUI 与其他程序）
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                info["reason"] = f"port {port} in use"
                _log("error", f"本地售后面板未启动：端口 {port} 已被占用")
                return info
    except Exception:
        pass

    try:
        handler = type("_BoundHandler", (_Handler,), {})
        srv = ThreadingHTTPServer(("0.0.0.0", port), handler)
        srv.daemon_threads = True
        srv.dist_dir = dist_dir
        srv.api_base = api_base
        t = threading.Thread(target=srv.serve_forever, name="local-web-server", daemon=True)
        t.start()
        _server = srv
        info["started"] = True
        info["reason"] = f"dist={dist_dir}"
        _log("info", f"本地售后面板已启动: http://localhost:{port} （静态目录 {dist_dir}，API 反代 {api_base}）")
        return info
    except Exception as e:
        info["reason"] = str(e)
        _log("error", f"本地售后面板启动失败: {e}")
        return info


def stop_local_web_server():
    """停止本地 Web 服务（程序退出时由 atexit 自动调用，一般无需手动调）。"""
    global _server
    if _server is not None:
        try:
            _server.shutdown()
        except Exception:
            pass
        _server = None


if __name__ == "__main__":
    # 独立运行便于测试：python -m core.local_web_server [--port 8787] [--dir <dist>]
    import argparse
    _p = argparse.ArgumentParser()
    _p.add_argument("--port", type=int, default=DEFAULT_PORT)
    _p.add_argument("--dir", default="")
    _a = _p.parse_args()
    if _a.dir:
        os.environ["LOCALWEB_DIST_OVERRIDE"] = _a.dir

    _d = _resolve_dist_dir()
    if not _d:
        sys.exit("dist not found")
    srv = ThreadingHTTPServer(("0.0.0.0", _a.port), type("_H", (_Handler,), {}))
    srv.daemon_threads = True
    srv.dist_dir = _d
    srv.api_base = DEFAULT_API_BASE
    print(f"serving {_d} on http://localhost:{_a.port} (api -> {DEFAULT_API_BASE})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
