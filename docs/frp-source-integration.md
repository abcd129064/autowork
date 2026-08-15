# frp 源码接入调研报告（frpc.exe → frp-dev 源码）

> 调研日期：2026-08-13
> 调研对象：`autowork`（Python/PySide6）当前通过 `QProcess` 调用内置 `frpc.exe` 建立 XTCP 隧道；工作区 `frp-dev`（frp 完整 Go 源码，版本 **0.70.1**）为潜在替代来源。
> 当前内置二进制版本：**frpc.exe 0.66.0**。

---

## 1. 调研背景与目标

autowork 的 XTCP 远程隧道能力目前依赖项目目录下的 `frpc.exe` 二进制（`core/frp_remote.py` 用 `QProcess` 启动并传入 TOML 配置）。本次调研回答两个问题：

1. **理解 (a)**：能否用 frp-dev 源码**自行编译 frpc.exe** 替换现有二进制，且不改变 autowork 现有外部行为？
2. **理解 (b)**：能否**完全不再依赖 exe**，把 frp 作为 **Go 库嵌入**运行（进程内或自研包装器）？

调研结论必须保证：XTCP 隧道、端口绑定、TOML 配置格式等用户可见行为与现有实现兼容。

---

## 2. 现状分析：autowork 对 frpc.exe 的全部依赖点

### 2.1 依赖清单（文件 + 行号 + 用途）

| # | 文件 | 行号 | 用途 |
|---|------|------|------|
| 1 | `core/frp_remote.py` | L44-46 | 两个 TOML 文件名常量：`frpc_xtcp_panel.toml`（frpc 实际加载）、`frpc_xtcp.toml`（手工 visitor 持久化/启动恢复） |
| 2 | `core/frp_remote.py` | L49-54 | frp 服务器默认配置（`49.235.34.253:7900`、token 认证） |
| 3 | `core/frp_remote.py` | L79-99 | `_parse_visitors_toml()`：**正则解析** TOML 的 `[[visitors]]` 段（serverName/secretKey/bindPort），用于启动恢复 |
| 4 | `core/frp_remote.py` | L120-131 | `_load_manual_toml()`：启动时从 `frpc_xtcp.toml` 恢复手工 visitor 注册表（不启动 frpc） |
| 5 | `core/frp_remote.py` | L133-241 | visitor 注册表管理：`register_visitor` / `ensure_visitor` / `remove_visitor` / `used_ports` / `_port_owner`（端口分配与冲突检测） |
| 6 | `core/frp_remote.py` | L249-250 | `is_running()`：以 QProcess 引用非空判定 frpc 运行状态 |
| 7 | `core/frp_remote.py` | **L252-272** | `apply()`：**重写 TOML → 停旧 frpc → `QProcess.start(frpc_exe, ["-c", toml_path])` 启动新 frpc**；L259-261 检查 `frpc.exe` 存在性 |
| 8 | `core/frp_remote.py` | **L274-286** | `_stop_frpc()`：断开信号连接 + `proc.kill()` **强制终止**（非优雅关闭） |
| 9 | `core/frp_remote.py` | L288-292 | `_on_stop_cleanup_done()`：进程停止后的 `deleteLater` 清理 |
| 10 | `core/frp_remote.py` | **L294-301** | `_on_frpc_finished()`：frpc **意外退出**处理（记录退出码、状态复位、下次连接自动重启） |
| 11 | `core/frp_remote.py` | **L303-315** | `_on_frpc_output()` / `_on_frpc_error()`：**读取 stdout/stderr 转发到 `log_message` 信号**（主窗口日志区展示） |
| 12 | `core/frp_remote.py` | **L317-354** | `_write_toml()` / `_write_manual_toml()` / `_write_visitor_blocks()`：**TOML 生成**（serverAddr/serverPort/auth.method/auth.token + `[[visitors]]` 块） |
| 13 | `core/frp_remote.py` | L358-390 | `open_session()`：注册 visitor + 启动 frpc 后 **延时等待隧道就绪**（新隧道 2500ms / 复用 300ms）再打开会话 |
| 14 | `core/frp_remote.py` | L459-468 | `shutdown()`：主窗口 closeEvent 统一停止 frpc + 关闭会话窗口 |
| 15 | `core/frp_remote.py` | L502-520 | `FrpRemoteBridge`：兼容薄包装（委托 manager） |
| 16 | `main_window/remote_mixin.py` | L72-76 | 订阅 `log_message`（→ 日志区）与 `frpc_state_changed`（→ 按钮状态刷新） |
| 17 | `main_window/remote_mixin.py` | L161-166 | 从 manager 恢复手工 visitor 列表 |
| 18 | `main_window/remote_mixin.py` | L630-652 | `_on_xtcp_connect()`：注册手工 visitor → `mgr.apply()` 启动 frpc；日志文案（L651） |
| 19 | `main_window/remote_mixin.py` | L654-677 | `_on_xtcp_disconnect()`：注销手工 visitor → `mgr.apply()`（剩余隧道为空时 frpc 停止）；日志文案（L663/673/675） |
| 20 | `windows/tunnel_panel.py` | L42-52 | 「当前隧道」面板：frpc 状态标签（L45）+ 全停按钮（L48-51） |
| 21 | `windows/tunnel_panel.py` | L79-93 | 订阅 `visitors_changed` / `frpc_state_changed`（closeEvent 显式断开） |
| 22 | `windows/tunnel_panel.py` | L101-138 | 表格展示 records + 每行「断开」按钮（→ `disconnect_visitor`） |
| 23 | `windows/tunnel_panel.py` | L158-171 | `_on_disconnect` / `_on_stop_all`：移除 visitor 并 `apply()` |
| 24 | `main_window/main_window.py` | L1275-1301 | `closeEvent` → `get_session_manager().shutdown()`（frpc 生命周期终结点） |
| 25 | `p2p.py` | L5-23 | `generate_random_port()`：随机端口生成（排除常用端口 + 已用端口，frpc 端口绑定前分配） |
| 26 | `p2p.py` | L26-35 | `is_port_in_use()`：端口占用探测 |
| 27 | `frpc_xtcp.toml` | 全文件 | 手工 visitor 持久化格式：`[[visitors]]` + name/type/serverName/secretKey/bindPort（见附录 A） |

### 2.2 依赖特征归纳

| 维度 | 现状实现 | 说明 |
|------|---------|------|
| 进程启动 | `QProcess.start(frpc_exe, ["-c", toml])` | frp_remote.py L270 |
| 配置输入 | 运行时**自生成 TOML** 文件，frpc 从文件加载 | L317-354 |
| 日志采集 | 读 QProcess **stdout/stderr** → `log_message` 信号 | L303-315 |
| 状态判定 | `is_running()`（QProcess 引用）＋ `frpc_state_changed` 信号 | L249-250 |
| 配置变更 | **kill 后重启** frpc（visitor 增删改时） | L262-270 |
| 停止 | `proc.kill()` 强制终止（无优雅关闭） | L285 |
| 端口分配 | Python 侧 `generate_random_port` 预分配 → 写入 TOML bindPort | p2p.py L5-23 |
| 意外退出 | `_on_frpc_finished` 复位状态，下次连接自动重启 | L294-301 |
| 生命周期 | 主窗口 closeEvent → `shutdown()` | main_window.py L1288-1289 |

> **关键结论**：autowork 与 frpc 的耦合面非常薄——仅"进程启动 + 配置文件 + stdout/stderr 日志"。TOML 内容、端口分配、状态展示全部由 Python 侧管理，**不依赖 frpc 的 admin API / web 界面**。这极大降低了替换风险。

---

## 3. frp-dev 源码可复用接口分析

### 3.1 版本确认

- `frp-dev`：`pkg/util/version/version.go` 中 `var version = "0.70.1"`；`go.mod` 要求 **Go 1.25.0**；module 路径 `github.com/fatedier/frp`。
- 当前 `frpc.exe`：**0.66.0**（`frpc --version` 实测）。
- 差异：0.66 → 0.70.1 之间 frp 的 **v1 TOML 配置格式保持稳定**（自 0.52 起 stable），xtcp visitor 的字段（name/type/serverName/secretKey/bindPort）未变化。

### 3.2 三个可复用入口

#### ① `pkg/sdk/client`（HTTP API 客户端——**不是嵌入库**）

文件：`pkg/sdk/client/client.go`（141 行）

| 函数 | 签名 | 说明 |
|------|------|------|
| `New(host, port)` | `(string, int) -> *Client` | 指向 frpc 的 **admin web server**（需在 TOML 中开启 `webServer.port`） |
| `SetAuth(user, pwd)` | — | admin 认证 |
| `GetProxyStatus(ctx, name)` / `GetAllProxyStatus(ctx)` | — | 查询隧道状态（`/api/status`） |
| `Reload(ctx, strictMode)` | — | 热重载配置（`/api/reload`） |
| `Stop(ctx)` | — | 请求 frpc 停止（`/api/stop`） |
| `GetConfig(ctx)` / `UpdateConfig(ctx, content)` | — | 读/写运行时配置（`/api/config`） |

**结论**：该 SDK 面向"frpc 已作为进程运行 + 已开启 admin 端口"的场景，是**状态查询/热重载的辅助工具**，不能替代进程本身。autowork 当前未开启 admin 端口、未使用任何 admin API——若走此 SDK 需在生成的 TOML 中新增 `webServer` 段，属于**新增能力**而非替换。

#### ② `client` 包 + `pkg/config` + `pkg/config/source`（**真正的库嵌入入口**）

官方 `cmd/frpc/sub/root.go`（220 行）完整演示了嵌入组装流程：

```go
// 1. 加载现有 TOML（直接兼容 autowork 生成的配置！）
result, err := config.LoadClientConfigResult(cfgFilePath, strictConfigMode)
// result.Common  → v1.ClientCommonConfig（serverAddr/port/auth/log/webServer...）
// result.Proxies → []v1.ProxyConfigurer
// result.Visitors→ []v1.VisitorConfigurer（xtcp visitor 在此）

// 2. 构建配置源聚合器（必填项）
configSource := source.NewConfigSource()
configSource.ReplaceAll(result.Proxies, result.Visitors)
aggregator := source.NewAggregator(configSource)   // store source 可选，本项目不需要

// 3. 创建并运行服务
svr, err := client.NewService(client.ServiceOptions{
    Common:                 result.Common,          // 必填
    ConfigSourceAggregator: aggregator,             // 必填（NewService 强制校验）
    UnsafeFeatures:         security.NewUnsafeFeatures(nil),
    ConfigFilePath:         cfgFilePath,            // 可选，仅日志/提示用
})
err = svr.Run(context.Background())                 // 阻塞直到 ctx 取消
```

**`Service` 公开方法**（`client/service.go`）：

| 方法 | 签名 | 说明 |
|------|------|------|
| `NewService(options)` | `(ServiceOptions) -> (*Service, error)` | 创建服务（同步完成首次配置加载与校验） |
| `Run(ctx)` | `(context.Context) -> error` | 阻塞运行；登录失败默认 `LoginFailExit` 退出 |
| `Close()` / `GracefulClose(d)` | — | 停止服务（GracefulClose 支持 kcp/quic 优雅收尾） |
| `UpdateAllConfigurer(proxies, visitors)` | — | **不重启**热更新隧道配置（ctl 层 diff 应用） |
| `UpdateConfigSource(common, proxies, visitors)` | — | 更新配置源并触发 reload（API /api/reload 同款路径） |
| `StatusExporter()` | `-> StatusExporter` | 查询隧道运行状态（`GetProxyStatus(name)`），**替代 admin HTTP API 的进程内等价物** |

**`v1.ClientCommonConfig` 关键字段**（`pkg/config/v1/client.go` / `common.go`）：`Auth`（token）、`ServerAddr/ServerPort`、`LoginFailExit`、`WebServer{Addr,Port,...}`（**Port=0 则不启动 admin**）、`Log{To,Level,MaxDays,DisablePrintColor}`、`Transport`、`VirtualNet`、`Store`（可选，本项目不需要）。

**日志接管**（`pkg/util/log/log.go`）：
- `log.InitLogger(logPath, level, maxDays, disableColor)`：`logPath="console"` 输出到 `os.Stdout`，否则写轮转文件。
- 自定义输出：底层 `github.com/fatedier/golib/log` 支持 `WithOutput(io.Writer)` 选项；frp 提供 `NewWriteLogger` 适配器，可把 frp 日志导入任意自定义 writer（文件/管道/网络）。

#### ③ 官方 CLI 组装模板（`cmd/frpc/sub/root.go` L123-219）

`runClient` → `runClientWithAggregator` → `startServiceWithAggregator` 三步就是"最小嵌入程序"的完整参考实现：加载配置 → 校验（`validation.ValidateAllClientConfig`）→ NewService → Run。自研二进制可直接复用此流程。

### 3.3 构建能力确认

`Makefile` L40-41：

```makefile
frpc:
	env CGO_ENABLED=0 go build -trimpath -ldflags "$(LDFLAGS)" -tags "frpc$(NOWEB_TAG)" -o bin/frpc ./cmd/frpc
```

- `make frpc` 即可编译（Linux 宿主上交叉编译 Windows 产物：`GOOS=windows GOARCH=amd64` 前缀环境变量即可，**纯 Go 无 cgo，交叉编译零障碍**）。
- `NOWEB_TAG` 存在时跳过 web 界面资源（frpc 的 admin UI），产物更小（当前 0.66.0 frpc.exe 为 16.7MB，不含 web 资源）。

---

## 4. 方案对比与取舍

### 方案 A：源码编译 frpc.exe 替换现有二进制（理解 (a)）

**做法**：
1. 在 frp-dev 目录执行（Windows 宿主直接构建，或 Linux 交叉编译）：
   ```bash
   cd frp-dev && make frpc            # 本机构建
   # 或交叉编译（在任意平台）:
   # GOOS=windows GOARCH=amd64 CGO_ENABLED=0 go build -trimpath -o bin/frpc.exe ./cmd/frpc
   ```
2. 将产物 `bin/frpc.exe` 复制到 autowork 根目录，覆盖现有 `frpc.exe`。
3. **Python 侧代码零改动**：QProcess 启动参数（`-c toml`）、TOML 格式、stdout/stderr 日志全部保持兼容。

**优点**：
- 改动最小（纯产物替换），现有行为完全一致，风险最低
- 可自由跟随上游版本（当前 0.66.0 → 0.70.1，含多年 bug 修复）
- 不引入任何新依赖/新维护面
- 若用 0.66.0 对应 tag 编译，可做到"行为逐字节一致"

**缺点**：
- 仍依赖外部进程 + 文件配置，无法获得"热更新不重启""进程内状态查询"等库形态能力
- 需要本机安装 Go 1.25+ 工具链（一次性成本）
- 版本差异（0.66→0.70.1）需冒烟验证（见 §6 风险）

### 方案 B：frp 作为 Go 库直接嵌入（理解 (b) 的"纯库"形态）

**两条子路径**：

| 子路径 | 做法 | 可行性 |
|--------|------|--------|
| B1：Go 库嵌入 **Python 进程**（cgo .pyd 扩展） | 用 cgo 把 frp 编译为 Python 扩展，Python 直接调 `Service.Run` | **不推荐**：frp 是纯 Go（goroutine 模型），cgo 扩展需 mingw/gcc 工具链、与 PySide6 事件循环集成困难、GIL 交互复杂、打包体积膨胀、跨 Python 版本构建脆弱 |
| B2：Go 库嵌入 **自研 Go 守护进程** | 见方案 C | **可行**（等价于方案 C） |

**纯库形态的技术可行性本身是成立的**（`client` 包 API 完备：加载 TOML → NewService → Run / UpdateAllConfigurer / StatusExporter / GracefulClose），但**对 autowork 这个 Python 应用而言，进程内嵌入没有落地点**——Go 库只能存在于 Go 进程中。

**结论**：理解 (b) 的"完全无 exe"对 autowork 不可行（除非接受 cgo 扩展的复杂代价）；但 frp 库的 API 形态可完整复用到方案 C。

### 方案 C：自研 Go 二进制（frp 库 + 项目定制能力）（理解 (b) 的落地形态）

**做法**：新建一个独立 Go module（可 `replace` 指向 frp-dev 或 fork），main 中按 `cmd/frpc/sub/root.go` 的流程组装：

```go
// main.go 核心骨架
result, _ := config.LoadClientConfigResult(*cfgPath, true)
configSource := source.NewConfigSource()
configSource.ReplaceAll(result.Proxies, result.Visitors)
aggregator := source.NewAggregator(configSource)
svr, _ := client.NewService(client.ServiceOptions{
    Common: result.Common, ConfigSourceAggregator: aggregator,
})
// 可扩展能力（相比原生 frpc）：
// 1. 监听 stdin/命名管道/HTTP，接收「重写配置」指令 → svr.UpdateAllConfigurer(...) 热更新
// 2. 定时/按需输出隧道状态 → svr.StatusExporter().GetProxyStatus(name)
// 3. 优雅退出：收到终止信号 → svr.GracefulClose(500ms)
svr.Run(ctx)
```

**对外契约保持与 frpc.exe 完全一致**：命令行 `-c toml`、stdout 日志、退出码语义——Python 侧 QProcess 逻辑可零改动接入。

**优点**：
- 获得库形态的全部收益：**visitor 增删改不重启**（替代现 kill+重启）、进程内状态查询（替代 admin HTTP API）、优雅关闭（替代 `proc.kill()`）
- 无 cgo、无 Python 侧改动、打包仍是一个 exe 放在 autowork 目录

**缺点**：
- 需要维护一个 Go 仓库（或 fork frp），引入持续维护成本
- 需要 Go 1.25+ 工具链 + 构建流水线
- 热更新等新能力需重新设计 Python 侧通信协议（如命名管道 JSON 指令），超出"纯替换"范畴

### 4.1 对比表

| 维度 | 方案 A（编译替换） | 方案 B（pyd 嵌入） | 方案 C（自研 Go 二进制） |
|------|------------------|-------------------|------------------------|
| Python 侧改动 | **零** | 大（新模块 + 构建链） | **零**（可零改动接入） |
| 外部行为一致性 | 完全一致 | 重构 | 完全一致（契约保持） |
| 依赖引入 | 无（仅 Go 工具链一次性） | cgo/mingw 工具链 | Go 工具链 + 新仓库 |
| 构建成本 | 低（一条 make 命令） | 高（跨语言扩展） | 中（自研 main + 维护） |
| 维护成本 | 最低（跟随上游） | 高 | 中（fork/replace 上游） |
| 热更新不重启 | ✗（现状 kill+重启） | ✗/复杂 | ✓ `UpdateAllConfigurer` |
| 进程内状态查询 | ✗（需开 admin API） | ✓ 但集成难 | ✓ `StatusExporter` |
| 优雅关闭 | ✗（现 kill） | ✗ | ✓ `GracefulClose` |
| 风险等级 | **低** | 高 | 中 |
| 版本对齐 | 0.66 → 0.70.1 需冒烟 | — | 同左 + 自研代码风险 |

### 4.2 推荐结论

**推荐：方案 A（先落地）→ 方案 C（可选演进）**

1. **立即执行方案 A**：用 frp-dev 源码（建议先对齐 0.66.0 对应 commit/tag 或直接 0.70.1 并冒烟验证）编译 `frpc.exe` 替换现有二进制。成本最低、风险最小、满足"使用源码替代 exe"的第一层诉求，且保持所有用户可见行为不变。
2. **演进到方案 C（按需）**：若后续出现明确痛点——「visitor 增删频繁导致隧道反复重启」「需要实时隧道状态展示」「frpc 强杀导致端口 TIME_WAIT 残留」——再基于 `client` 包构建自研 Go 包装器，Python 侧仍以 QProcess 接入（契约不变），仅升级通信协议。
3. **不推荐方案 B**（cgo .pyd 嵌入）：构建链复杂、与 PySide6/Qt 事件循环集成风险高、打包体积与可维护性均劣于 A/C。
4. **`pkg/sdk/client` 的定位**：无论 A/C，若未来需要隧道状态查询，优先用 `StatusExporter`（C）或 admin API（A 需在 TOML 增加 `webServer` 段）；SDK 仅作为 A 形态下的可选查询工具。

---

## 5. 落地步骤（方案 A，可直接执行）

### 步骤 1：准备 Go 工具链（一次性）

- 安装 Go 1.25+（`go version` 验证）。
- frp-dev 已含完整源码与 go.mod，**无需下载依赖到外网**（`go build` 会自动读取 vendor 或走 module cache；如离线受限，先 `go mod download` 或配置 GOPROXY）。

### 步骤 2：编译（二选一）

```bash
# 方式 1：本机（Windows）直接构建
cd c:\Users\shen_zhe\Desktop\frp-dev
make frpc        # 产物 bin\frpc.exe

# 方式 2：任意平台交叉编译
cd c:\Users\shen_zhe\Desktop\frp-dev
$env:GOOS="windows"; $env:GOARCH="amd64"; $env:CGO_ENABLED="0"
go build -trimpath -o bin\frpc.exe ./cmd/frpc
```

> 建议加 `-tags frpcnoweb`（Makefile 的 `NOWEB_TAG` 机制）跳过 web 资源，产物更小。

### 步骤 3：版本对齐冒烟（关键，见 §6）

1. 用现有 `frpc_xtcp_panel.toml` 手工执行：`frpc.exe -c frpc_xtcp_panel.toml`，观察是否成功登录 frp 服务器（日志出现 `login to server success`）。
2. 在 autowork 内走完整链路：添加 visitor → 连接 → SSH/SFTP/RDP 会话 → 断开 → 全停 → 退出程序（验证 `shutdown()` 路径）。

### 步骤 4：产物替换

- 将编译产物复制到 autowork 根目录覆盖 `frpc.exe`。
- 重新打包（`python build_exe.py`）或直接替换 `dist/AutoWork/frpc.exe`。

### 步骤 5（可选，方案 C 演进）

- 新建 module（如 `autowork-go/`），`go.mod` 中 `replace github.com/fatedier/frp => ../frp-dev`。
- 按 §3.2-② 组装 main；增加命名管道/HTTP 指令通道 + `UpdateAllConfigurer` 热更新 + `StatusExporter` 状态输出。
- Python 侧 `RemoteSessionManager.apply()` 从"kill+重启"改为"写 TOML + 发重载指令"（可选，渐进）。

---

## 6. 风险与兼容性注意事项

| 风险 | 影响 | 缓解 |
|------|------|------|
| **版本差异**（0.66.0 → 0.70.1） | 新版本配置解析/登录协议行为差异 | 先冒烟：`frpc -c` 手工验证登录 + 全链路会话测试；若异常，检出 0.66.0 对应 commit 编译（`git checkout v0.66.0` 或等价的源码快照） |
| TOML 格式兼容 | 现 TOML 含 `auth.method = "token"`、`[[visitors]]` 五字段 | v1 格式自 0.52 稳定，0.66→0.70.1 字段未变；冒烟覆盖 |
| `proc.kill()` 强杀 | 方案 A 保持现状（无回归）；方案 C 可改 `GracefulClose` | 不属于本次替换范围，记录为 C 的演进点 |
| 端口绑定 | bindPort 由 Python 预分配，frpc 仅绑定；替换不影响 | 无 |
| 日志解析 | Python 仅读 stdout/stderr 原始行；新版本日志格式微调不影响（均为自由文本） | 无 |
| Go 工具链缺失 | 编译失败 | 一次性安装 Go 1.25+；离线环境配置 GOPROXY 或 vendor |
| `frpc.exe` 缺失路径 | `apply()` L260-261 抛 `OSError` | 替换后保持同名同目录即可 |

---

## 7. 结论摘要

1. **autowork 与 frpc 的耦合面极薄**（进程启动 + TOML 文件 + stdout 日志），替换风险低。
2. **理解 (a)（编译替换 exe）= 方案 A**：可行且推荐，Python 代码零改动，一条 `make frpc` 完成。
3. **理解 (b)（库嵌入）**：frp 的 `client` 包 API 完备可嵌入，但"嵌入 Python 进程"（cgo .pyd）不可取；合理落地形态是**方案 C 自研 Go 包装器**，对外契约与 frpc.exe 一致，可零改动接入现有 QProcess，并获得热更新/状态查询/优雅关闭等增值能力。
4. **`pkg/sdk/client` 是 admin HTTP API 客户端**，不是嵌入库；仅在需要状态查询且保留进程模式时作为辅助使用。
5. **落地顺序**：先 A 后 C，按需演进；所有变更均不改变用户可见行为（XTCP 隧道、端口绑定、TOML 格式）。

---

## 附录 A：当前 TOML 配置样例（frpc_xtcp.toml）

```toml
[[visitors]]
name = "snk_1806"
type = "xtcp"
serverName = "snk_1806"
secretKey = "abc123"
bindPort = 47511
```

运行时实际加载的 `frpc_xtcp_panel.toml` 额外包含 server 段：

```toml
serverAddr = "49.235.34.253"
serverPort = 7900
auth.method = "token"
auth.token = "<token>"
```

## 附录 B：关键源码位置索引（frp-dev）

| 内容 | 文件 |
|------|------|
| 嵌入入口 Service / ServiceOptions | `client/service.go` L64-95（Options）、L162-223（NewService）、L225-274（Run）、L413-420（Close/GracefulClose）、L368-411（热更新） |
| 官方组装模板 | `cmd/frpc/sub/root.go` L123-219 |
| 配置加载 | `pkg/config/load.go` L327-404（LoadClientConfigResult） |
| 配置源 | `pkg/config/source/`（source.go / aggregator.go / store.go） |
| TOML 解码（v1） | `pkg/config/v1/decode.go`、`client.go`、`visitor.go` |
| HTTP API 客户端 | `pkg/sdk/client/client.go` |
| 日志 | `pkg/util/log/log.go` L42-68（InitLogger）、L94-109（WriteLogger） |
| 构建 | `Makefile` L40-41（frpc 目标） |
| 版本 | `pkg/util/version/version.go`（0.70.1） |

---

# 第二部分：密钥安全与动态配置专项调研（2026-08-13 追加）

> 本部分回答两个问题：
> 1. 能否随机生成 `[[proxies]]` 的 `secretKey` 与 `auth.token`（或加密/引用方式），使**直接复制的配置无法使用**；密钥入库/入日志、**每月轮换一次**，并**提供 API 接口**供其他后台使用。
> 2. frpc 改为**动态加载** `[[proxies]]` 的可行性（当前每增一条需重启 frpc 才生效）。
>
> 说明：autowork 使用的是 `[[visitors]]`（XTCP **访问端**），`[[proxies]]` 是隧道**对端**（设备端 frpc）。两类的动态加载机制完全对称（store API 各有一套端点），本部分以 visitors 为主给出证据，结论同时适用于 proxies。

---

## 3. 问题一：密钥安全（secretKey / auth.token）

### 3.1 frp 官方能力盘点（源码 + 版本确认）

| 能力 | 机制 | 引入版本 | 当前 frpc.exe 0.66.0 | frp-dev 0.70.1 | 适用字段 |
|------|------|----------|---------------------|----------------|----------|
| **tokenSource** | 配置只写 `auth.tokenSource.type = "file"` + `path`，实际 token 存 0600 文件；**启动时解析一次，不支持运行时重载**（官方文档明确） | v0.64.0 | ✅ | ✅（源码另有 `exec` 类型：子进程 stdout） | `auth.token`（frpc 端 `pkg/config/v1/client.go` L200-202，frps 端 `server.go` L132） |
| **TOML 模板** | 加载配置前先做 Go template 渲染：`{{ .Envs.FRP_AUTH_TOKEN }}` 从**进程环境变量**取值（`pkg/config/load.go` L84-114，`glbEnvs` 在 init 时快照） | 极老（INI 时代即有） | ✅ | ✅ | **任意字段**（含 `secretKey`） |
| webServer /api/reload | 管理员 HTTP API，重读配置文件并热应用（`client/config_manager.go` L26-54） | 老 | ✅ | ✅ | — |
| **store API** | `/api/store/proxies`、`/api/store/visitors` 全套 CRUD（`client/api_router.go` L44-54） | **v0.68.0** | ❌ | ✅ | — |

**关键结论：**
- `auth.token`：可用 `tokenSource`（官方推荐）或 env 模板，TOML 内**无明文**。
- `secretKey`：**没有** `secretKeySource` 变体（`pkg/config/v1/visitor.go` L36、`proxy.go` L477 均为普通 string 字段），**只能走 env 模板**（或启动前由 autowork 写入明文——不推荐）。
- env 模板在 frpc **启动时**渲染：TOML 文件里永远只有 `{{ .Envs.XXX }}` 占位符，**直接复制配置无法使用** ✅ 恰好满足需求。

### 3.2 密钥生命周期设计（autowork 侧 + 服务器 API）

```
┌─ 生成 ── autowork（Python secrets.token_urlsafe(32)）
│          本地持久化：settings.json 透明加密字段 / 独立密钥文件（0600）
├─ 上报 ── POST 服务器 API 备案（历史可查，入数据库 / 日志）
├─ 轮换 ── 每月一次：新 token/secretKey 生成 → 服务器 frps 更新并重启（低峰窗口）
│          → autowork 更新本地 + 重启 frpc（auth 变更必须重启，见 3.3）
└─ 下发 ── 服务器 API 供其他后台（设备端 frpc 配置系统等）拉取当前有效密钥
```

**服务器侧 API 规范（供其他后台使用）：**

| 接口 | 方法 | 说明 |
|------|------|------|
| `/api/frp/keys` | GET | 当前生效的 token / 各 visitor secretKey（需鉴权：API Key / Basic Auth） |
| `/api/frp/keys/rotate` | POST | 手动触发轮换（生成新密钥、更新 frps token 文件、记录历史、返回新值） |
| `/api/frp/keys/history` | GET | 轮换历史（kind / value / valid_from / valid_to / rotated_at / rotated_by） |

数据库建议表：`frp_keys`（kind、value、valid_from、valid_to、status、rotated_at、rotated_by）；每月轮换由服务器 cron 或 autowork 本地定时器触发。

### 3.3 关键约束与风险（必须明示）

1. **auth.token 是 frps 与所有 frpc 共享的**。frps 的认证器在启动时一次性构建（`server/service.go` L161 `BuildServerAuth`），**不支持热更新** → 每月轮换 token 必须**重启 frps**（全量隧道中断一次，需选低峰窗口）并同步所有 frpc（autowork 本地 + 设备端）。
2. **secretKey 是隧道两端一致的**：autowork 是 visitor 端，secretKey 必须与**对端设备 frpc 的 XTCP proxy secretKey** 完全相同。单方面轮换会导致隧道失联 → 轮换需设备端同步（经用户后台统一下发）或**仅对用户可控的设备**轮换。
3. **tokenSource 不支持运行时重载**：token 文件改了也必须重启 frpc/frps 才生效（frpc 重启成本低；frps 重启成本高）。
4. token 轮换失败风险：新 token 若未同步，frpc 登录即失败（`loginFailExit` 默认 true，进程退出）→ 设计"先验证后切换"：frps 侧先应用新 token 并验证一条 frpc 登录成功，再批量下发。
5. 轮换后旧密钥可保留一段过渡期（valid_to 记录）便于回溯，但 frp 本身**不支持双 token**。

---

## 4. 问题二：frpc 动态加载 `[[proxies]]` 的可行性

### 4.1 结论先行：**可行**，且存在两条官方路径，均无需重启 frpc 进程

代码证据链（frp-dev 0.70.1）：

```
POST /api/store/visitors（JSON）              ← client/api_router.go L50-54
  └→ serviceConfigManager.CreateStoreVisitor   ← client/config_manager.go L278-299
      └→ withStoreVisitorMutationAndReload     ← L416-440（写 store 文件后立即）
          └→ svr.reloadConfigFromSourcesLocked  ← 热应用，无需重启
              └→ Control.UpdateAllConfigurer    ← client/control.go L285-287
                  ├→ vm.UpdateAll(visitorCfgs)  ← visitor_manager.go L134-182（diff：删旧/启新）
                  └→ pm.UpdateAll(proxyCfgs)    ← 与 proxies 对称
```

`visitor_manager.go` L134-182 确认：新增 visitor 会即时 `startVisitor`，修改的 visitor 会被 Close 后重建，删除的即时 Close —— **visitors/proxies 均可热增删改**。

### 4.2 两条落地路径对比

| 维度 | 路径 A：重写 TOML + GET /api/reload | 路径 B：store API 增量 CRUD |
|------|-------------------------------------|------------------------------|
| frpc 版本要求 | **0.66.0（当前）即可**（/api/reload 老功能） | 需 **≥0.68**（0.66.0 无 store API）→ 走方案 A 源码编译 0.70.1 |
| 操作方式 | autowork 保留现有"重写整份 TOML"逻辑，只把 kill+重启 换成 `GET /api/reload` | `POST/DELETE /api/store/visitors` 增量增删，不重写整份 TOML |
| 持久化 | TOML 文件本身 | store JSON 文件（`store.enable=true` + `store.path`），与 TOML 中 visitor 并存（Aggregator 合并两源，`pkg/config/source/aggregator.go` L70-96） |
| 进程行为 | frpc **常驻**，控制连接不断，隧道平滑切换 | 同左 |
| 额外要求 | 开启 `webServer.port`（Basic Auth：user/password） | 同左 + `store.enable` |
| 改动量 | 小（改 apply() 一处 + TOML 模板化） | 中（新增 API 客户端封装） |
| 适用阶段 | **短期先落地** | **中期（编译 0.70.1 后）演进** |

### 4.3 与 autowork 现有代码的对接点

- `core/frp_remote.py` L252-272 `apply()`：改为"frpc 未运行则启动；运行中则重写 TOML → `GET /api/reload`"（token 变更除外，见 3.3）。
- `core/frp_remote.py` L274-286 `_stop_frpc()`：仅显式停止 / token 轮换时调用，不再作为 apply 的默认路径。
- `core/frp_remote.py` L317-354 `_write_toml()`：TOML 改为模板占位（`auth.token = "{{ .Envs.FRP_AUTH_TOKEN }}"`、`secretKey = "{{ .Envs.FRP_SECRET_<snk> }}"`、`webServer.password = "{{ .Envs.FRP_ADMIN_PASSWORD }}"`），QProcess 启动前 `setProcessEnvironment` 注入。
- `core/frp_remote.py` L79-99 `_parse_visitors_toml()`：注册表恢复时读取模板占位符即可（展示用），实际值不落 TOML。
- 管理端口：frpc `webServer.port` 建议固定专用端口（`p2p.py` 已排除 7400 等常用端口，可复用该逻辑）。
- 密钥轮换：新增密钥管理模块（生成/上报/轮换）+ 服务器 API 客户端；token 轮换后 frpc 重启一次（auth 启动时构建）。

### 4.4 待实测验证点

1. 0.66.0 的 `/api/reload` 是否完整支持 visitors 热增删（0.70.1 源码已确认；0.66 需冒烟验证，若不支持则直接走路径 B 升级）。
2. reload 期间正在传输的 XTCP 会话是否中断（理论：仅新配置 diff 应用，既有 visitor 不变则不断）。
3. env 模板在 Windows QProcess 场景下的转义（`{{ .Envs.XXX }}` 与 TOML 引号的组合）。

---

## 5. 综合结论与推荐路线

| 问题 | 结论 | 推荐落地 |
|------|------|----------|
| 1a. token 防明文 | ✅ `tokenSource`（0.66.0 可用）或 env 模板 | TOML 模板化 + 密钥文件/环境变量注入 |
| 1b. secretKey 防明文 | ✅ 仅 env 模板一条路（无 Source 字段） | 同上，逐 visitor 独立环境变量 |
| 1c. 随机生成 + 每月轮换 + 入库 + API | ✅ 工程可行 | autowork 密钥管理模块 + 服务器 API（§3.2） |
| 1d. 直接复制配置不可用 | ✅ TOML 只含占位符 | 模板/引用双机制保证 |
| 2. 动态加载 proxies/visitors | ✅ 官方支持（reload 或 store API） | 路径 A 短期落地（0.66.0 零升级）→ 路径 B 随方案 A 升级后演进 |

**总体推荐**：
1. **立即（不动 frpc 版本）**：TOML 全面模板化（env 注入）+ webServer 开启 + `apply()` 改为 reload 热更新——解决"复制配置可用"与"加 visitor 需重启"两个痛点。
2. **密钥治理**：autowork 内新增密钥管理（生成/上报/本地持久化/月轮换），服务器侧实现 §3.2 的 API 与数据库表，轮换走低峰窗口。
3. **中期**：按第一部分方案 A 编译 frpc 0.70.1 替换，启用 store API（路径 B），并可用 `auth.tokenSource` 替代 env 模板（更规范）。
4. **前提条件**：secretKey 轮换需设备端 frpc 同步（用户后台统一下发）；auth.token 轮换需 frps 重启窗口。这两个前提不满足时，只能先做"防明文"，轮换延后。

---

## 附录 C：模板化 TOML 样例（落地后形态）

```toml
serverAddr = "49.235.34.253"
serverPort = 7900
auth.method = "token"
auth.token = "{{ .Envs.FRP_AUTH_TOKEN }}"

webServer.addr = "127.0.0.1"
webServer.port = 7400
webServer.user = "frp"
webServer.password = "{{ .Envs.FRP_ADMIN_PASSWORD }}"

store.enable = true
store.path = "frpc_store.json"   # 仅路径 B（frpc ≥0.68）

[[visitors]]
name = "snk_1806"
type = "xtcp"
serverName = "snk_1806"
secretKey = "{{ .Envs.FRP_SECRET_SNK_1806 }}"
bindPort = 47511
```

复制该文件到任何机器直接运行 `frpc -c` 都会因缺少环境变量而**渲染失败/密钥为空**，即"加密文本直接复制不可用"。

