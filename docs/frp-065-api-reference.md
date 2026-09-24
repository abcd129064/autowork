# frp 0.65 管理 API 参考手册

> 适用范围：frps / frpc **v0.65.0**（当前生产锁定版本）。
> 全部条目均对照 tag 源码核验：
> - frps：`server/dashboard_api.go`（`registerRouteHandlers`）
> - frpc：`client/admin_api.go`（`registerRouteHandlers`）
> 文档生成时间：2026-09-22。升级 frp 前请勿直接套用更高版本端点（见 §4 版本墙）。

autowork 集成位置：
- frps 感知：`core/frps_admin.py`
- frpc 控制：`core/frp_remote.py`
- UI：`windows/remote_session/remote_hub.py`

---

## 0. 两套 API，互不相同

| | frps（服务端 dashboard） | frpc（本机 admin） |
|---|---|---|
| 开关 | `frps.toml` 的 `webServer.addr` + `port` | `frpc.toml` 的 `webServer.addr` + `port` |
| 默认地址 | 生产 `0.0.0.0:7400` | 本机 `127.0.0.1:{adminPort}` |
| 能干什么 | 读全局代理/流量/服务信息、清离线代理 | 读/写本机配置、热重载、优雅停止、查本机代理状态 |
| autowork 用途 | 在线感知 + 概览卡 | apply 热重载 + 优雅停止 + 通道自检 |

> ⚠️ 客户端（本机）HTTP 请求务必 `ProxyHandler({})` 强制直连：开发机
> `HTTP(S)_PROXY` 调试代理会对回环/自建服务端伪造 502「假可达」。

---

## 1. 鉴权

两端 webServer 共用 `pkg/util/http` 的 AuthMiddleware：

- `webServer.user` + `webServer.password` → **HTTP Basic Auth**（`Authorization: Basic base64(user:pass)`）
- 否则回退 `auth.token`（frps）→ 校验控制连接 token
- autowork 走 **Basic Auth**（生产 frps = `admin` / `abc123`，口令存 `frps_admin` credentials 域，DPAPI 加密落盘）

> 🔒 生产弱口令 + `auth.token=123` + 面板全网开放为已知风险，改密需重启 frps（在线设备瞬断重连），待维护窗口处理。

---

## 2. frps API（`server/dashboard_api.go`）

### 2.1 `GET /healthz`
- 免鉴权，存活探测，`200` 空 body。

### 2.2 `GET /api/serverinfo`
- **服务端体检**。autowork 已接入（会话总览「frps 概览卡」）。
- 响应：
  ```json
  {
    "version": "0.65.0",
    "bindPort": 7000,
    "vhostHTTPPort": 0,
    "vhostHTTPSPort": 0,
    "tcpmuxHTTPConnectPort": 0,
    "kcpBindPort": 0,
    "quicBindPort": 0,
    "subdomainHost": "",
    "maxPoolCount": 0,
    "maxPortsPerClient": 0,
    "heartbeatTimeout": 0,
    "allowPortsStr": "",
    "tlsForce": false,
    "totalTrafficIn": 2097152,
    "totalTrafficOut": 1048576,
    "curConns": 12,
    "clientCounts": 9,
    "proxyTypeCounts": { "xtcp": 767, "tcp": 3 }
  }
  ```
- 关键字段：`version`、`clientCounts`（在线 frpc 数）、`curConns`（当前连接数）、`totalTrafficIn/Out`（今日）、`proxyTypeCounts`（各类型代理总数，复数）。

### 2.3 `GET /api/proxy/{type}`
- **按类型列全部代理**。`{type}` ∈ `tcp|udp|http|https|stcp|xtcp|tcpmux`。
- autowork 用 `xtcp` 作为在线感知唯一数据源（`/api/proxy/xtcp`）。
- 响应 `{"proxies":[ ... ]}`，每条 `ProxyStatsInfo`：
  ```json
  {
    "name": "snk_4007",
    "conf": { "...代理完整配置（含 base + 类型字段）..." },
    "clientVersion": "0.65.0",
    "todayTrafficIn": 1024,
    "todayTrafficOut": 512,
    "curConns": 1,
    "lastStartTime": "2026-09-22T10:00:00+08:00",
    "lastCloseTime": "",
    "status": "online"
  }
  ```
- **status 语义（在线权威判据）**：代理在 `pxyManager` 中可查 → `online`；否则 → `offline`。
  autowork 四态口径：`online` / `offline` / `unregistered`（名单无此 snk）/ `None`（感知不可用，必须回退一期行为，绝不阻塞连接）。
- 结果按 `name` 字典序升序。

### 2.4 `GET /api/proxy/{type}/{name}`
- 单条代理精确查询（`GetProxyStatsResp`），字段同 2.3 单条（无 `clientVersion`）。
- 无此代理 → `404 {"code":404,"msg":"no proxy info found"}`。
- 用途：把预检从「拉全量名单」缩到「只查一个 snk」，生产 767 条时更省带宽（当前用批量一次拿全，暂未切换）。

### 2.5 `GET /api/traffic/{name}`
- **单条代理流量历史**。⚠️ 修正认知：底层是 `DateCounter`，返回的是**按天聚合**的近若干日总量，**不是秒级/分钟级实时分桶**。
  ```json
  { "name": "snk_4007", "trafficIn": [ ... ], "trafficOut": [ ... ] }
  ```
  - `trafficIn/Out` 为定长数组（默认 `ReserveDays = 7`），逐格 = 该日字节总量，索引 0 为今日。
  - 想画「隧道实时趋势」不能只靠这个端点（粒度是天级）；autowork 的本地 RTT sparkline（`core/visitor_probe.py`）才是细粒度来源。
- 无此代理 → `404`。

### 2.6 `DELETE /api/proxies?status=offline`
- **清理服务端累积的 offline 代理记录**（内存态统计）。
- `status` **只接受 `offline`**，否则 `400`。
- 成功日志 `cleared [N] offline proxies, total [M]`；body `200`。
- 用途：长时间运行的 frps 若积累了大量注销设备的 offline 残留，可定期清理。**这是 frps 唯一的写端点**，autowork 感知模块坚持纯只读，不触碰。

### 2.7 `GET /metrics`（可选）
- 仅当 `frps.toml` 配 `enablePrometheus = true` 时注册；Prometheus 抓取格式，需鉴权。
- autowork 不接（需另配 Prometheus，成本高、收益低）。

### 2.8 视图端点
- `/` → 301 跳 `/static/`；`/static/`、`/favicon.ico` = 内置 Web dashboard 静态资源。API 集成不关心。

---

## 3. frpc API（`client/admin_api.go`）

### 3.1 `GET /healthz`
- 免鉴权，存活探测。autowork「frpc 通道自检」第一步。

### 3.2 `GET /api/reload[?strictConfig=true]`
- **热重载**：重读 frpc 配置文件并 `UpdateAllConfigurer(proxyCfgs, visitorCfgs)`，frpc 内按 `name + DeepEqual` diff，未变化的 visitor/proxy 零触碰。
- 校验失败（配置非法/validate 不通过）→ `400`，**当前进程不受影响**（autowork 据此保留原进程并抛错，绝不拿坏配置重启）。
- `strictConfig=true` 开启严格模式。
- ⚠️ reload 不重读 common（serverAddr/auth 等启动级参数）→ token/服务器地址变更须停旧起新，不能靠 reload。
- autowork `apply()` 三分支：在跑且签名未变 → 重写 TOML + reload；签名变化或 admin 不可达（含重试）→ 停旧起新；reload 4xx → 保留原进程抛错。

### 3.3 `POST /api/stop`
- **优雅停止**：`go svr.GracefulClose(100ms)`，frpc 自行收尾退出。
- ⚠️ 方法必须是 **POST**（GET 不匹配路由）。
- autowork `_stop_frpc` 三段式：POST /api/stop（应用级优雅）→ `proc.terminate()`（进程级优雅，SIGTERM）→ 2.5s QTimer 回检兜底 `proc.kill()`，全异步不阻塞 GUI。⚠️ QProcess 无 `quit()`（那是 QThread/QEventLoop 的方法）。

### 3.4 `GET /api/status`
- 本机各代理**运行态**。响应 `StatusResp = { 类型: [ {name,type,status,err,local_addr,plugin,remote_addr}, ... ] }`。
  ```json
  {
    "xtcp": [
      {"name":"snk_4007","type":"xtcp","status":"start","err":"",
       "local_addr":"","plugin":"","remote_addr":"..."}
    ]
  }
  ```
- `status` 是 frpc 本地 phase（如 `start`/`start error`），与 frps 侧 `online/offline` 是两套口径：前者=本机有没有把这个 visitor 拉起来，后者=服务端连接是否存活。autowork「通道自检」同时看两者。

### 3.5 `GET /api/config`
- 返回 **frpc 配置文件原文**（`os.ReadFile(configFilePath)`），body 为文件字节。
- frpc 无配置文件路径（纯命令行启动）→ `400 "frpc has no config file path"`。
- 用途：配置查看/备份/差异比对。

### 3.6 `PUT /api/config`
- **覆写 frpc 配置文件**：请求体整体写入 `configFilePath`（权限 `0600`）。
  - body 空 → `400`；写盘失败 → `500`。
- ⚠️ 只写文件，不自动 reload；要生效须再 `GET /api/reload`。
- 用途：远程下发/程序化改隧道配置（autowork 目前自己管 TOML，未用此端点）。

> frpc **无** `/api/clients`、`/api/store/*`、`/api/v2/*`（见 §4）。

---

## 4. 版本墙：0.65 **做不到**、升级才解锁

| 端点/能力 | 引入版本 | 0.65 | 说明 |
|---|---|---|---|
| frps `/api/clients`、`/api/clients/{key}` | **0.67** | ❌ | 客户端 login 列表/版本汇总；0.65 想按客户端聚合只能从 `/api/proxy/xtcp` 的 `conf` 反推 |
| frps `GET /api/proxies/{name}`（无 type） | **0.67** | ❌ | 0.65 只有带 `{type}` 的 `GET /api/proxy/{type}/{name}` |
| frpc `/api/store/proxies`、`/api/store/visitors` 全套 CRUD | **0.68** | ❌ | 增量增删隧道、不重写整份 TOML；0.65 只能走「重写 TOML + /api/reload」 |
| frps 全部 `/api/v2/*`（users/system/clients/proxies） | **0.70** | ❌ | 新 dashboard 后端；本地 `frp-dev/`（0.70+）里有，**不要照抄到 0.65** |

本地仓库 `frp-dev/` 是 0.70+ 源码，仅作实现参考；线上跑 0.65，§2/§3 的 tag 核验清单才是权威。三期升级 frp 后再回来解锁 §4 能力（`/api/clients` 客户端视图、store API 增量隧道、SQLite 趋势）。

---

## 5. autowork 端点使用矩阵

| 端点 | 用在哪 | 频率 / 触发 |
|---|---|---|
| frps `/api/proxy/xtcp` | 在线感知（`frps_admin.refresh`） | 默认 30s 周期 + 进页 + 「立即感知」 |
| frps `/api/serverinfo` | frps 概览卡 | 随 proxies 成功轮 best-effort 追加（失败不熔断、不改通道态） |
| frps `/healthz` | — | 未用（有 proxies 兜底存活判断） |
| frps `/api/traffic/{name}` | — | 未用（天级粒度不满足实时趋势需求） |
| frpc `/healthz` | 通道自检 | 「frpc 通道自检」按钮 |
| frpc `/api/status` | 通道自检 | 「frpc 通道自检」按钮 |
| frpc `/api/reload` | apply 热重载 | 隧道增删且签名未变时 |
| frpc `/api/stop` | 优雅停止 | 「优雅停止 frpc」按钮 / 关闭流程 |
| frpc `/api/config` | — | 未用（autowork 自持 TOML 落盘） |

> 设计底线：`frps_admin` 感知通道**只发只读 GET**，概览/名单任何异常都静默降级为「—」，绝不阻塞建会话，绝不误判离线。
