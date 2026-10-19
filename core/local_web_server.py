# -*- coding: utf-8 -*-
"""本地售后面板 Web 服务：桌面程序运行时顺带开一个本地端口，浏览器即可访问售后面板网页。

- 静态资源：托管售后面板 Web 前端构建产物（web/aftersale_front/dist，
  打包环境位于 _internal/web_dist/，开发环境直接读仓库内 dist/）
- /api/*：反向代理到云端售后面板 API（与线上站点同一数据源，接口/口径完全一致）
- 纯标准库实现（http.server + urllib），零新增依赖；daemon 线程，绝不阻塞/影响 GUI

静态文件只从构建产物目录读取，并对 URL 路径做两次规范化校验，挡住 ../ 穿越。
反代只转发到云端，本地不落任何业务数据。

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

# 显式列出扩展名映射而不用 mimetypes：后者依赖 Windows 注册表，同一份
# 代码在不同机器上可能把 .js 返回成 text/plain，浏览器会拒绝执行模块
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
    """探测前端构建产物目录，返回第一个含 index.html 的候选。

    顺序为环境变量覆盖、打包资源、开发仓库 dist、app_dir/web_dist：
    从「显式指定」到「约定位置」，让测试与绿色版都能不改代码切换路径。
    以 index.html 是否存在作为判据，因为失败的前端构建会留下空目录，
    只判目录存在会把 404 当成已就绪。
    """
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
    # 日志本身不能成为故障源：conn_logger 在独立运行或初始化早期可能不可用，
    # 抛异常会连带把请求处理线程打挂
    try:
        from core.conn_logger import conn_logger
        getattr(conn_logger, level if level != "warn" else "error")("LOCALWEB", msg)
    except Exception:
        try:
            print(f"[local_web] {level}: {msg}")
        except Exception:
            pass


class _Handler(BaseHTTPRequestHandler):
    """请求处理器：静态文件直出，/api/ 前缀反向代理到云端。

    dist_dir 与 api_base 挂在 server 实例上而不是类属性：同一进程可能先后
    起过多个 server（测试或重启），类属性会互相覆盖；property 每次从当前
    server 取，天然隔离。
    """

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
        """发送完整响应，统一补 Content-Length 与 no-cache。"""
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        # 浏览器提前关标签页会产生 BrokenPipe/ConnectionReset，属于正常现象；
        # 吞掉即可，否则控制台会刷一屏无意义的栈
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_json(self, code, obj):
        """以 UTF-8 发送 JSON；ensure_ascii=False 保留中文，便于排查。"""
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    # ---- 静态文件 ----
    def _serve_static(self, path: str):
        """把 URL 路径映射到 dist 下的文件，找不到时按 SPA 规则回退。"""
        rel = path.lstrip("/") or "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        # 先 normpath 再判前缀：规范化会消掉 ../ 与重复分隔符，后续判断
        # 才能在规范形式上比较
        full = os.path.normpath(os.path.join(self.dist_dir, rel))
        # 防路径穿越（含 %2e%2e 等 URL 编码变体：解码后复检）
        root = os.path.normpath(self.dist_dir)
        if not full.startswith(root):
            return self._send_json(403, {"error": "forbidden"})
        # %2e%2e 在首次 normpath 前不会暴露，解码后必须再规范化复检一次；
        # 只查原始路径会漏掉编码变体
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
        # Vite 产出的 assets 文件名带内容哈希，可以长缓存；index.html 引用
        # 这些哈希文件名，必须每次回源，否则发版后用户一直拿到旧入口
        cache = "public, max-age=31536000, immutable" if "/assets/" in rel.replace("\\", "/") else "no-cache"
        self._send(200, body, ctype, {"Cache-Control": cache})

    # ---- API 反代 ----
    def _proxy(self):
        """把请求原样转发到云端 API，并把上游状态码与响应体透传回来。"""
        qs = self.path.partition("?")[2]
        # self.path 已含查询串，直接拼在 api_base 后；不做 URL 重写，
        # 前端与线上站点共用同一套接口路径
        url = f"{self.api_base}{self.path}"  # self.path 已含 /api/... 与 query
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        # 只转发方法、请求体与 Content-Type：Host/Authorization 等头由
        # urllib 按目标地址重算，转发过去反而可能泄露本地信息
        req = urllib.request.Request(url, data=body, method=self.command)
        ct = self.headers.get("Content-Type")
        if ct:
            req.add_header("Content-Type", ct)
        try:
            with urllib.request.urlopen(req, timeout=_API_TIMEOUT) as resp:
                data = resp.read()
                self._send(resp.status, data, resp.headers.get("Content-Type", "application/json"))
        except urllib.error.HTTPError as e:
            # 4xx/5xx 是业务语义的一部分（如 401 未登录），必须原样透传；
            # 统一包成 502 会让前端分不清「没登录」和「服务挂了」
            self._send(e.code, e.read(), e.headers.get("Content-Type", "application/json"))
        except Exception as e:
            # 连接超时、DNS 失败等归为网关错误，前端据此提示「云端不可达」
            self._send_json(502, {"error": f"upstream unreachable: {e}"})

    # /api/ 是唯一走反代的前缀；其余路径一律按静态资源处理
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
    """按 settings['local_web'] 启动本地 Web 服务，幂等且绝不阻塞 GUI。

    启动失败只记录日志并写进返回值的 reason，不向调用方抛异常：本地 Web
    是附加能力，不该因为端口被占或前端没构建就让主程序起不来。
    返回 enabled/started/port/url/reason 五个字段，调用方可据此提示或重试。
    """
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
    # 模块级单例：重复调用直接返回已运行的实例，避免同端口二次 bind
    if _server is not None:
        info["started"] = True
        info["reason"] = "already running"
        return info

    dist_dir = _resolve_dist_dir()
    if not dist_dir:
        info["reason"] = "web_dist not found (frontend not built)"
        _log("error", f"本地售后面板未启动：未找到前端构建产物（{info['reason']}）")
        return info

    # 先探测端口：connect_ex 成功说明已有服务在听（可能是另一个 AutoWork
    # 实例），此时静默跳过，绝不抢占用户正在用的端口
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
        # 动态子类把配置挂到 server 实例：BaseHTTPRequestHandler 每个请求
        # 都会新建 handler，只有 server 对象能跨请求保存 dist_dir/api_base
        handler = type("_BoundHandler", (_Handler,), {})
        # 监听 0.0.0.0 而不是 127.0.0.1：售后现场常用手机或同事电脑打开同一
        # 面板；本地不存业务数据，反代只把请求转给云端
        srv = ThreadingHTTPServer(("0.0.0.0", port), handler)
        srv.daemon_threads = True
        srv.dist_dir = dist_dir
        srv.api_base = api_base
        # daemon=True：主程序退出时线程随进程结束，不需要额外 join；
        # 否则关闭窗口会被一个仍在 serve_forever 的线程拖住
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
    """停止本地 Web 服务；程序退出时由 atexit 自动调用，一般无需手动调。

    shutdown 会阻塞到 serve_forever 退出，但线程是 daemon，进程退出时
    即使没走到这里也不会留下孤儿进程。
    """
    global _server
    if _server is not None:
        try:
            _server.shutdown()
        except Exception:
            pass
        _server = None


if __name__ == "__main__":
    # 独立运行便于测试：python -m core.local_web_server [--port 8787] [--dir <dist>]
    # 独立模式不启 GUI、不读 settings.json，端口与目录全由命令行决定，改前端时可快速预览
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
