// 生产布局本地仿真：/v2/** 走本地 dist-v2，其余全部转发到生产 nginx。
// 用途：在部署前验证 dist-v2 这个真实产物在生产布局下能否跑通。
// 用法：node tools/serve_dist_v2.mjs [port]
import http from "node:http";
import fs from "node:fs";
import path from "node:path";

const PORT = Number(process.argv[2] || 8899);
const PROD = { host: "49.235.34.253", port: 80 };
// 产物在 web/vue-pure-admin/dist-v2/
const DIST = "C:/Users/shen_zhe/Desktop/autowork/web/vue-pure-admin/dist-v2";

const MIME = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
  ".wasm": "application/wasm",
  ".mp3": "audio/mpeg",
  ".mp4": "video/mp4"
};

function sendFile(res, fp) {
  const ext = path.extname(fp).toLowerCase();
  const body = fs.readFileSync(fp);
  res.writeHead(200, {
    "Content-Type": MIME[ext] || "application/octet-stream",
    "Content-Length": body.length,
    "Cache-Control": "no-store"
  });
  res.end(body);
}

const server = http.createServer((req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);

  // ---- /v2/** 本地产物 ----
  if (url.pathname === "/v2" || url.pathname.startsWith("/v2/")) {
    let rel = url.pathname.slice(3); // 去掉 "/v2"
    if (rel === "" || rel === "/") rel = "/index.html";
    // Windows path.normalize 产生反斜杠，必须统一分隔符后比较
    const fp = path.normalize(path.join(DIST, rel));
    if (!fp.startsWith(path.normalize(DIST + path.sep))) {
      res.writeHead(403).end("forbidden");
      return;
    }
    if (fs.existsSync(fp) && fs.statSync(fp).isFile()) {
      sendFile(res, fp);
      return;
    }
    // SPA fallback
    sendFile(res, path.join(DIST, "index.html"));
    return;
  }

  // ---- 其余全部转发到生产（真实 /api 与真实 /v1 行为） ----
  const pReq = http.request(
    {
      host: PROD.host,
      port: PROD.port,
      path: req.url,
      method: req.method,
      headers: { ...req.headers, host: PROD.host }
    },
    pRes => {
      res.writeHead(pRes.statusCode, pRes.headers);
      pRes.pipe(res);
    }
  );
  pReq.on("error", e => {
    res.writeHead(502, { "Content-Type": "text/plain; charset=utf-8" });
    res.end("proxy error: " + e.message);
  });
  req.pipe(pReq);
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`[prod-emu] http://127.0.0.1:${PORT}/v2/  ->  ${DIST}`);
  console.log(`[prod-emu] 其余路径 -> http://${PROD.host}:${PROD.port}`);
});
