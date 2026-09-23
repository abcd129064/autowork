# AutoWork 接口文档

本文档描述 AutoWork 项目各模块的公开接口（类、函数、信号），供开发维护参考。

---

## 目录

- [core/ 基础层](#core-基础层)
- [workers/ 后台线程层](#workers-后台线程层)
- [database/ 数据层](#database-数据层)
- [windows/ 独立窗口层](#windows-独立窗口层)
- [main_window/ 主窗口层](#main_window-主窗口层)
- [main_window/ Hub 页面（二级界面）](#main_window-hub-页面二级界面2026-09)
- [win_api/ Windows API 层](#win_api-windows-api-层)
- [tools/ 独立工具模块](#tools-独立工具模块)
- [p2p.py P2P 工具模块](#p2ppy-p2p-工具模块)
- [Web 端接口（售后面板 Web）](#web-端接口售后面板-web)
- [配置门面 config/（原 settings.json）](#配置门面-config原-settingsjson2026-09-06-拆分)

---

## core/ 基础层

### core.app_paths

应用路径解析（兼容 PyInstaller 打包）。

| 函数 | 签名 | 说明 |
|------|------|------|
| `get_app_dir()` | `() -> str` | 获取应用根目录。开发环境=项目根；打包环境=exe 所在目录 |
| `get_resource_dir()` | `() -> str` | 获取只读资源目录（styles/ 等）。打包环境=`sys._MEIPASS`（_internal/） |

---

### core.conn_logger

SSH/SFTP 连接统一文件日志 + Qt 消息处理器。

#### 类 `ConnLogger`

线程安全的文件日志器，单文件超过 2MB 自动轮转。

| 方法 | 签名 | 说明 |
|------|------|------|
| `info(op, msg, **kw)` | 记录 INFO 级别日志 | kw 可含 host/port/user |
| `error(op, msg, **kw)` | 记录 ERROR 级别日志 | 同上 |
| `exception(op, msg, exc, **kw)` | 记录异常（含完整调用栈） | exc 为异常对象 |

**模块级单例**：`conn_logger = ConnLogger()`

#### 模块级函数

| 函数 | 说明 |
|------|------|
| `qt_message_handler(msg_type, context, message)` | Qt 消息处理器：将 Warning/Critical/Fatal 级别消息落盘；经 `qInstallMessageHandler` 注册 |

---

### core.utils

通用工具函数。

| 函数/常量 | 签名 | 说明 |
|-----------|------|------|
| `classify_conn_error(e)` | `(Exception) -> str` | 将网络异常转为中文用户友好提示 |
| `natural_sort_key(s)` | `(str) -> list` | 自然排序 key（数字段按数值比较） |
| `safe_close_transport(transport, join_timeout=3)` | 安全关闭 paramiko Transport | close + join 等待线程退出 |
| `cleanup_log_dir(dir_path, max_files=500, max_age_days=30, suffix='.log')` | `(str, int, int, str) -> int` | 日志目录闭环清理（超龄/超量），返回删除文件数，失败静默降级 |
| `show_info_bar(message, message_type="info", title=None, duration=2500, parent=None)` | `(...) -> InfoBar` | **统一 InfoBar 提示**：位置固定 BOTTOM_RIGHT，标题按类型自动映射（success→成功/info→提示/warning→警告/error→错误），默认时长 2500ms（<=0 常驻）；parent 缺省取当前活动窗口；返回 bar 实例供追加 Action/Widget |
| `launch_sibling_app(exe_name, args=None)` | `(str, list?) -> bool` | 拉起同包分发的独立 exe（如 aftersale.exe），开发环境自动映射到 dist/，失败返回 False |
| `PARAMIKO_AVAILABLE` | `bool` | paramiko 是否可用（环境探测） |
| `RETRYABLE_KEYWORDS` | `tuple` | 可重试错误关键词 |
| `RETRY_MAX` | `int = 5` | 最大重试次数 |
| `RETRY_DELAY` | `int = 2` | 重试间隔（秒） |

> 提示规范：全项目 InfoBar 显示统一走 `show_info_bar()`，禁止各模块直调 `InfoBar.success/error/...`（样式、位置、标题映射、时长集中维护）。主窗口 `MainWindow._show_info_bar` 为兼容入口，内部转调本函数。

---

### core.acrylic_patch

亚克力效果 PIL 替代补丁。在 `main.py` 最顶部导入：

```python
import core.acrylic_patch  # noqa: F401
```

- 使用 `importlib.util.find_spec('numpy')` 零副作用探测
- numpy/scipy 不可用时注入 PIL 实现的 `gaussianBlur` 到 `sys.modules['qfluentwidgets.common.image_utils']`
- 必须在 qfluentwidgets 任何导入之前执行

---

### core.perf

运行时性能开关中心（亚克力/动画/表格平滑滚动，切换即时生效，经 `core.app_settings` 门面持久化到 `config/perf.json`；兼容旧字段 `performance_mode` 与旧 settings.json 键位自动迁移）。

#### 全局开关

| 函数 | 签名 | 说明 |
|------|------|------|
| `is_acrylic_enabled()` | `() -> bool` | 亚克力磨砂效果是否开启（默认 true） |
| `set_acrylic_enabled(enabled)` | `(bool)` | 设置亚克力开关（持久化 + 生效） |
| `is_animation_enabled()` | `() -> bool` | 菜单弹出动画是否开启（默认 true） |
| `set_animation_enabled(enabled)` | `(bool)` | 设置动画开关（持久化 + 生效） |
| `is_performance_mode()` | `() -> bool` | 兼容旧接口：亚克力关闭即视为性能模式 |
| `is_table_smooth_scroll_enabled()` | `() -> bool` | TableWidget 平滑滚动动画是否开启（**默认关闭**：大表格逐帧重绘卡顿） |
| `set_table_smooth_scroll_enabled(enabled)` | `(bool)` | 设置全局表格平滑滚动开关（持久化 + 生效） |
| `invalidate_cache()` | `()` | 使运行时缓存失效（下次读取重载 config/perf.json） |

#### 面板级覆盖（面板开关单独生效，未设置回退全局）

| 函数 | 签名 | 说明 |
|------|------|------|
| `get_table_smooth(panel=None)` | `(str?) -> bool` | 生效的表格平滑滚动；panel ∈ aftersale/video/management/remote |
| `set_table_smooth(panel, enabled)` | `(str?, bool)` | 设置面板级（panel 非空，键 `perf_table_smooth_<panel>`）或全局开关 |
| `apply_table_smooth_mode(table, panel=None)` | `(TableWidget, str?)` | 把当前生效值应用到单个 TableWidget（开=LINEAR / 关=NO_SMOOTH，即时） |
| `apply_table_smooth_globally()` | `()` | 全局开关变更后刷新所有已打开窗口的表格（按 覆盖→全局 逐窗生效） |
| `get_animation(panel=None)` | `(str?) -> bool` | 生效的弹出动画；panel ∈ aftersale/video |
| `set_animation(panel, enabled)` | `(str?, bool)` | 设置面板级或全局动画开关 |

#### 中央补丁（main.py 启动时调用一次，幂等）

| 函数 | 说明 |
|------|------|
| `patch_menu_animation()` | 拦截 qfluentwidgets 菜单/ComboBox 下拉弹出动画，按「面板覆盖→全局」生效值降级 |
| `patch_dialog_animation()` | 拦截 MaskDialogBase 弹窗淡入/淡出：动画关闭时直接显示（规避整窗离屏渲染卡顿） |
| `patch_table_hover_repaint()` | 拦截 TableWidget hover 重绘：鼠标扫过行只重绘新旧两行条带（替代库默认整视口重绘，≈1/23 面积） |
| `patch_mica_policy()` | 云母环境兜底（2026-09-24）：DWM 在 **RDP 会话** / **系统透明效果关闭**（含省电模式自动关）时静默不渲染 Mica backdrop，而 qfw 已把窗口背景置全透明 → "同一份产物有的电脑没云母"。命中时双层短路——①`FluentWidget.setMicaEffectEnabled` 开关路径强制不开（背景保持实色）；②`WindowsWindowEffect.setMicaEffect` no-op（FluentWidget 基类 FramelessWindow 在 Win11 分支构造时**绕过开关直调**此方法，必须一并拦）。判定：`mica_block_reason()` ∈ below-win11 / rdp-session / transparency-off / ''（支持）。环境支持时零 patch；不支持时窗口回退纯主题色背景，日志记 `[perf] Mica 云母已禁用（原因）` |

#### 轻量表格委托（LeanTableDelegate 开关，配 `core.lean_table_delegate`）

| 函数 | 说明 |
|------|------|
| `is_lean_delegate_enabled()` | 是否启用轻量表格委托（**默认开启**；关闭即回退 qfluentwidgets 自带委托） |
| `set_lean_delegate_enabled(enabled)` | 设置轻量委托开关并持久化；已打开的表格一并切换（即时生效） |
| `patch_lean_table_delegate()` | 新建的 qfluentwidgets 表格自动挂轻量委托（幂等，启动时调用一次） |
| `apply_lean_delegate_globally()` | 按当前开关刷新所有已存在表格的委托（设置页切换后立即生效） |

---

### core.app_settings

**配置门面（2026-09-06 起 settings.json 已拆分下线）**：原单文件 settings.json 的 51+ 顶层键按域拆分为 `config/` 目录 8 个域文件，本模块是唯一读写入口（按键自动路由 + 进程缓存 + RLock + 敏感域透明 DPAPI 加解密）。

#### 域文件映射（`DOMAIN_FILES`）

| 域 | 文件 | 代表键 | 加密 |
|----|------|--------|------|
| aftersale | config/aftersale.json | `aftersale_cycle`、`aftersale_quick_phrases`、`aftersale_last_creator/resolver` | 否 |
| perf | config/perf.json | `perf_acrylic`、`perf_animation`、`perf_table_smooth*` | 否 |
| database | config/database.json | `mysql_sync`、`data_retention` | ✅ DPAPI |
| credentials | config/credentials.json | `ssh_pass`、`upload_*`、`api_credentials`、`ai_api_keys`、`frpc_server`、`deepseek_api_key` | ✅ DPAPI |
| ui | config/ui.json | `theme_*`、`font_*`、`dpi_scale`、`classic_layout`、`log_highlight_rules` | 否 |
| paths | config/paths.json | `exe_dir`、`videos_dir` 等 11 个路径键 | 否 |
| remote | config/remote.json | `remote_sessions`、`tcp_servers`、`restore_remote_sessions` | 否 |
| misc | config/misc.json | `web_port` 及一切未登记键兜底 | 否 |

未在 `KEY_DOMAIN` 登记的键一律归 misc 域（动态键如 `shortcut_*`、`ssh_commands`、`local_web` 即走此兜底，调用方无需登记）。

| 函数 | 签名 | 说明 |
|------|------|------|
| `get(key, default=None)` | `(str, Any) -> Any` | 按键读配置（自动路由所属域；容器值返回深拷贝防缓存污染） |
| `set(key, value)` | `(str, Any) -> bool` | 按键合并写（域内其他键不动；加密域自动加密落盘） |
| `remove(key)` | `(str) -> bool` | 按键删除（迁移清理旧字段等场景） |
| `get_domain(domain)` | `(str) -> dict` | 整域明文副本（深拷贝） |
| `update_domain(domain, data)` | `(str, dict) -> bool` | 锁内读现值→覆盖同键→落盘→失效缓存 |
| `replace_domain(domain, data)` | `(str, dict) -> bool` | 整域覆写（不合并） |
| `get_merged()` | `() -> dict` | 全域合并视图，**语义等同旧 settings.json 整文件**（明文），供「读整文件后多处取键」的调用点零逻辑改动迁移 |
| `invalidate_cache()` | `()` | 清空进程缓存（外部手改配置文件后的刷新入口） |
| `domain_of(key)` | `(str) -> str` | 键所属域 |
| `config_dir()` / `domain_path(domain)` / `legacy_settings_path()` | `() -> str` | config/ 目录 / 域文件路径 / 旧 settings.json 路径 |
| `migrate_legacy()` | `() -> bool` | 旧 settings.json 按域分拣 + 敏感字段加密 + 改名 `.bak`（幂等，只补缺键不覆盖新值） |

**迁移约定**：任何读写前惰性触发 `_ensure_migrated()`——应用首启自动完成拆分（旧文件保留为 settings.json.bak 可回滚），升级用户无感。**例外**：`database/backend.py` 与 `database/data_retention.py` 为避免导入 core 包触发 PySide6 依赖链，直接读 `config/database.json`（字段级只读，不经门面）。

---

### core.local_web_server

本地售后面板 Web 服务：daemon 线程托管前端静态页 + 反代云端 API，浏览器访问 `http://localhost:<port>`。GUI 主程序启动时调用，失败仅记日志不影响桌面功能。

| 函数 | 签名 | 说明 |
|------|------|------|
| `start_local_web_server(settings)` | `(dict) -> dict` | 按 `settings['local_web']`（缺省 enabled=True, port=8787）启动，幂等；返回 `{started, url, ...}` |
| `stop_local_web_server()` | `()` | 停止服务（程序退出时由 atexit 自动调用） |

---

### core.frps_admin

frps admin API 感知客户端（frps 0.65 v1 端点，**只读**，二期 P0）。权威数据源 `GET /api/proxy/xtcp`：现场 frpc 注册 xtcp proxy 且控制连接存活 ⇔ status=online。另经 `GET /api/serverinfo`（概览）与 `GET /api/proxy/{type}` ×8 + `GET /api/clients`（frps 网页面板 Proxies 页同源清单，best-effort）扩展展示。

**状态口径**（`online()` 返回值）：`online`（名单内且在线，放行建会话）/ `offline`（名单内但掉线）/ `unregistered`（名单内无此 snk）；感知不可用返回 `None`——调用方必须按「未知」回退纯本地行为，绝不阻塞连接。

**设计约束**：纯 GET 零写端点；连续 3 次失败熔断静默 60s（只停请求不清缓存，缓存超 TTL 90s 后查询自动降级为未知）；凭据走 credentials 域（DPAPI），仅内存持有；HTTP 强制空 `ProxyHandler` 直连（绕本机调试代理）。

#### 类 `FrpsAdminClient`

frps 感知客户端（进程级单例，主线程 QTimer 周期刷新；感知 HTTP 一律在后台线程执行，绝不阻塞 GUI）。

**信号**：`proxies_changed` dict（xtcp 名单刷新成功）、`serverinfo_changed` object（概览，失败发 None）、`all_proxies_changed` dict（全类型清单）、`channel_state_changed` str（ok/unreachable/unauthorized/error/unconfigured）、`refresh_finished` str（request_refresh 完成回执，queued 投递主线程供 UI 一次性反馈）。

| 方法 | 签名 | 说明 |
|------|------|------|
| `start()` | `()` | 启动周期感知（配置缺失/关闭时状态 unconfigured，不报错） |
| `stop()` | `()` | 停止周期感知 |
| `restart_timer()` | `()` | 配置面板改 URL/凭据/间隔后热生效（清缓存重读配置并立即感知一次） |
| `request_refresh()` | `()` | 后台线程执行 refresh（定时器周期与「立即感知/测试连接」统一入口；完成经 refresh_finished 回执） |
| `refresh()` | `() -> str` | 同步拉取 xtcp 名单并返回通道状态（熔断静默期内直接返回当前状态不发请求） |
| `online(snk)` | `(str) -> str?` | 感知 snk 在线态 online/offline/unregistered；不可用返回 None |
| `info(snk)` | `(str) -> dict?` | proxy 明细（curConns/lastStartTime/流量），未感知到返回 None |
| `serverinfo()` | `() -> dict?` | frps 概览（version/bindPort/curConns/clientCounts/流量/proxyTypeCounts） |
| `all_proxies()` | `() -> dict` | 全类型代理清单 {type: [proxy,…]}（proxy 字段含 name/user/clientID/status/conf 等）；过期返回空 dict |
| `client_version(client_id)` | `(str) -> str` | proxy.clientID → frpc 版本（网页面板 ClientVersion 列同源） |
| `snapshot()` | `() -> dict` | UI 一次性快照（state/fresh/age_sec/proxies/serverinfo，总览表与球桌页富集用） |
| `configured()` | `() -> bool` | 是否已配置 base_url |

**模块级单例**：`get_frps_client()`（懒建；首次获取不自动 start，由 UI 初始化显式启动）。

---

### core.visitor_probe

XTCP visitor 连接质量探测（二期 P1）：向 visitor 本地 bindPort 发起 TCP connect 计时，直连场景下该 RTT 就是 SSH 建连体感的忠实指标（frps 对 visitor 侧流量不可见）。后台守护线程串行轮询（默认 30s 一轮 / 单次 connect 超时 800ms，`frp_quality` 配置可调）；连续 N 次超时（默认 3）判「bad」（在线但不可达，区分于 frps 离线）；**冷洞首拍例外**（2026-09-23 P1-3）：无样本隧道首轮用 3s 长超时给首次打洞留时间，其结果不进样本、不计连续失败（`warmed` 标记保证只此一次）；环形缓冲每隧道 120 点（约 1 小时），仅内存不落库。评级阈值：≤60ms 优 / ≤150ms 良 / ≤300ms 一般。

| 函数 | 签名 | 说明 |
|------|------|------|
| `tcp_connect_rtt_ms(host, port, timeout_ms)` | `(str, int, int) -> float?` | TCP connect 计时 ms；失败/超时返回 None（模块级：单测 monkeypatch 注入，不碰真实网络） |

#### 类 `VisitorProber`

visitor RTT 探测调度器（进程级单例）。targets provider 约定：可调用 → {snk: bindPort}（默认接 `RemoteSessionManager`：仅 frpc 运行中且已注册未禁用的 visitor）。

**信号**：`sample` (snk, rtt_ms 或 None=超时)、`changed`（一轮探测完成，UI 刷新触发）、`verdict_changed` snk（判定翻转 ok↔bad 时告警刷新）。

| 方法 | 签名 | 说明 |
|------|------|------|
| `start()` / `stop()` | `()` | 启停后台探测线程（stop join 最多 2s） |
| `run_once()` | `()` | 串行探完一轮（探测线程定时调用；UI「立即探测」可直接调，与调度线程启停无关；隧道消失自动清理其样本） |
| `stats(snk)` | `(str) -> dict` | 单隧道质量快照：verdict/grade/avg_ms/p95_ms/samples/ok/recent |
| `verdict(snk)` | `(str) -> str` | 当前判定 ok/bad/unknown |

**模块级单例**：`get_prober()`。

> 坑：探测调度线程只 emit Qt 信号（queued 到主线程），不触碰任何控件；prewarm（开机预热打洞）**不复用**本模块 `run_once`——探测 800ms 超时是 RTT 口径，首洞 1~3s 会整轮误记超时污染质量页。

---

### core.updater

自动更新核心逻辑（纯逻辑层，无 Qt 依赖，可单测；线程与信号适配在 `workers/update_worker.py`，UI 编排在 `main_window/update_mixin.py`）。链路：检查 → 下载 → 校验 → 解压 staging → 拉起外部 updater → 主程序退出 → updater 三向原子换位 → 重启 → 回执消费（`update_result.log` 防死循环绝不重试）。SOP 见 docs/auto_update_research.md §7.4。

**关键常量**：`DEFAULT_UPDATE_BASE_URL`（生产更新源）、`CHANNEL_AUTOWORK` / `CHANNEL_AFTERSALE`（渠道名）、`PENDING_FLAG`（`.update-pending` 安装发起标记）、`RESULT_LOG`（updater 回执）、`STAGING_DIRNAME`（`_update_staging`）、`EXCLUDE_DIRS`（更新覆盖时排除的用户数据目录 config/logs/database）。

| 函数 | 说明 |
|------|------|
| `parse_version(v)` | 解析 `3.11.273-branch` → (3,11,273)；用 search 容忍 `v` 前缀（match 会解析成 (0,0,0) 致**永不更新**）；失败返回 (0,0,0) |
| `is_newer(remote, local)` | 远端版本是否新于本地 |
| `fetch_latest(base_url, channel, timeout=8.0)` | 拉 latest.json 抽取 channel 条目（兼容 channels/扁平两形态）；⚠️ 校验响应确实是 JSON——nginx SPA try_files 回退会以 200+text/html 返回首页 |
| `check_update(base_url, local_version, channel)` | 有新版本返回 latest 条目（附加 `_resolved_url` / `_remote_version`），否则 None |
| `sha256_of_file(path, chunk=256KB)` | 分块计算文件 sha256 |
| `download_file(url, dest, expect_sha256="", timeout=20.0, progress_cb=None, should_stop=None)` | 流式下载到 `.part` → 校验 sha256 → 原子改名；`should_stop` 回调返回 True 时中止（删 .part 抛 InterruptedError）；⚠️ Content-Type 含 HTML 即抛错（拦截 SPA 回退假包） |
| `verify_and_extract_zip(zip_path, dest_dir)` | zip 完整性校验 + 防 zip-slip 越界路径 + 解压；坏包抛 ValueError |
| `flatten_extract_root(dest_dir, main_exe="")` | onedir zip 常带一层顶级目录，返回真正含主 exe 的安装根 |
| `manifest_diff(files, local_root)` | 增量清单与本地 sha256 对比，返回需下载条目 |
| `download_incremental(base_url, files, staging_dir, progress_cb=None, should_stop=None)` | 逐文件下载到 staging（保持相对路径），进度为跨文件全局累计 |
| `build_bat(install_dir, staging_dir, main_exe, wait_pid, mode, result_log="", new_dir="", bak_dir="")` | 渲染 updater bat（所有路径生成期内联，运行期零参数零引号风险；外部命令全部 System32 绝对路径防 PATH 污染；vbs 引号用 Chr(34) 拼） |
| `write_updater_scripts(work_dir, install_dir, staging_dir, main_exe, wait_pid, mode)` | 写 update.bat + launcher.vbs（GBK 落盘；bat 放 %TEMP% 不进安装目录——整目录 rename 不能把 updater 卷进去） |
| `mark_update_pending(app_dir, info)` | 写 `.update-pending` 安装发起标记（重启后消费） |
| `consume_update_receipt(app_dir)` | 启动时消费回执；无 pending 返回 None；有 pending 无回执（updater 没跑完）返回失败并清理——**绝不重试安装**，防「每次启动重试失败」死循环 |
| `launch_updater(app_dir, staging_dir, main_exe, mode="full", pending_info=None, pid=None, work_dir="")` | 写标记 → 生成脚本 → vbs 隐藏拉起（失败回退直跑 bat 闪黑窗）；调用方成功后应尽快退出主程序 |
| `launch_updater_and_exit(...)` | `launch_updater` 兼容封装（拉起后返回 True） |
| `resolve_mode(entry, local_version)` | full/incremental 决策：本地低于 `min_version` 时增量强制降级全量 |
| `resolve_package_url(base_url, entry)` | package.url → 绝对下载地址 |
| `prepare_update(base_url, entry, app_dir, main_exe="", progress_cb=None, should_stop=None, local_version="")` | 下载 + 校验 + 解压 staging（full/incremental）；staging 放安装目录同级（同卷 move 才瞬时）；失败清理脏 staging，zip 保留复用 |
| `get_install_info()` | {app_dir, main_exe, frozen, local_version} |
| `get_update_base_url(settings_get=None)` | 更新源地址（`update_base_url` 配置优先，默认生产地址） |

---

### core.log_rules

日志高亮规则：默认值与编译函数。自原 settings_dialog 迁移（2026-09-07 项目清理）。

**常量**：`DEFAULT_LOG_RULES` —— 默认规则列表 `[{name, pattern, color, notify}]`：「错误」（红，通知）/「警告」（橙，静默）/「返回」「加分」「add」（旧版硬编码关键词迁移，橙，静默）。

| 函数 | 签名 | 说明 |
|------|------|------|
| `compile_log_rules(raw_rules)` | `(list) -> list` | 把规则配置编译为可匹配对象列表（非法正则跳过不抛错） |

---

### core.lean_table_delegate

轻量表格委托 LeanTableDelegate（滚动性能优化 P0-1）：保留 qfluentwidgets 表格全部视觉，仅替换文本绘制路径（去掉逐 cell 控件级绘制开销），大表格滚动帧率显著提升。开关与全局挂载见 `core.perf` 轻量委托组（`patch_lean_table_delegate` 启动时幂等挂载，设置页可即时切换回退）。

#### 类 `LeanTableDelegate`

继承 qfluentwidgets `TableItemDelegate`，仅覆写 `paint`：文本走原生 QStyle 绘制路径，勾选列/富文本语义不变；配合 `core/ops_link_delegate.py` 的 OpsLeanDelegate 提供操作列链接形态。

---

### core.ops_link_delegate

表格「操作列」文字链接委托：单元格内自绘多段可点击文本（零子控件，rebuild 无 cellWidget 卡顿问题）。**常量**：`LINKS_ROLE`（UserRole+2，链接定义存 item data）。

#### 类 `OpsLeanDelegate(_OpsLinkMixin, LeanTableDelegate)` / `OpsPlainDelegate(_OpsLinkMixin, TableItemDelegate)`

- `OpsLeanDelegate`：轻量文本绘制 + 操作列文字链接（**默认形态**）
- `OpsPlainDelegate`：库委托绘制 + 操作列文字链接（轻量委托关闭时的回退形态）
- `_OpsLinkMixin`（内部混入）：`init_ops_links` 绑定点击回调、`eventFilter` 悬停反馈与吞双击、`editorEvent` 命中判定、`teardown_ops_links` 委托被取代时摘过滤器防残留实例响应

| 函数 | 签名 | 说明 |
|------|------|------|
| `install_ops_links(table, handler)` | `(TableWidget, callable) -> delegate` | 把表格当前委托换成带操作链接的同族委托，返回新委托 |
| `rebuild_ops_delegate(table)` | `(TableWidget)` | `core.perf` 全局刷新委托时调用（保持链接能力不丢） |

---

### core.switch_cn_patch

SwitchButton 状态文本中文化补丁：qfluentwidgets SwitchButton 默认状态文本为英文 "On"/"Off"，本补丁统一改为中文「开/关」。

| 函数 | 签名 | 说明 |
|------|------|------|
| `patch_switch_cn_text()` | `()` | 状态文本中文化（幂等，重复调用无害；main.py 启动时调用） |

---

### core.frp_remote

frpc 管理 + 统一远程会话中心（XTCP 隧道 / SSH / SFTP / RDP 会话协调）。

| 函数/常量 | 签名 | 说明 |
|-----------|------|------|
| `get_session_manager()` | `() -> RemoteSessionManager` | 获取全局会话管理器单例 |
| `SOURCE_MANUAL` / `SOURCE_SNK` / `SOURCE_TABLE` | `str` | visitor 来源标记（手工添加 / snk 快捷 / 球桌库选择） |

#### 类 `RemoteSessionManager(QObject)`

| 信号 | 类型 | 说明 |
|------|------|------|
| `log_message` | `Signal(str)` | frpc 日志转发（主窗口日志区订阅） |
| `frpc_state_changed` | `Signal(bool)` | frpc 运行状态变化 |
| `tunnel_issue_changed` | `Signal(str, str)` | (snk, "lost"/"ok") 隧道失联/恢复翻转（P2-5，`report_tunnel_issue` 上报去重后发出，联动已开会话面板提示条） |
| `prewarmed` | `Signal(list)` | 预热成功的 snk 列表（预热线程 queued 回主线程，标记 `prewarmedAt` 仅内存态） |

| 方法 | 说明 |
|------|------|
| `open_session(kind, snk, table_id, notifier=None, source="")` | 建立远程会话（kind: ssh/sftp/rdp），自动确保 frpc 运行与隧道就绪；就绪等待为**端口监听轮询**（P2-4：200ms 间隔、8s 上限，就绪即开，不再固定延时） |
| `disconnect_visitor(server_name)` | 隧道面板「断开连接」：仅 frpc 运行中生效（返回 ok/not_running/not_found/error），先关相关会话再把 visitor 置 `disabled` 态并 apply 摘除隧道、释放端口；**注册与持久化保留**（记录落 `frpc_xtcp_disabled.json` 侧车，不进 frpc TOML），重新连接自动恢复启用，绝不自动启动 frpc |
| `delete_visitor(server_name)` | 隧道面板「删除 snk」：从注册表与持久化文件（含 disabled 侧车）彻底移除，frpc 未运行时也可执行且不启动 frpc |
| `active_count()` | 启用中（非 disabled）隧道数——静默预连必要性判定 |
| `autostart()` | 开机静默预连：自动拉起 frpc 恢复启用隧道（返回 started/restarted/reloaded/skipped_running/skipped_no_tunnel/failed），绝不弹窗抛错；失败自动**有界重试**（P1-2：60s/120s 各一次，成功清零并补预热）；配置键 `frp_autostart`（misc 域，默认开） |
| `prewarm_async()` | 预热打洞：daemon 线程对全部启用隧道 bindPort 串行 TCP connect（3s 长超时，与 RTT 探测 800ms 口径分离，不污染质量样本）；成功经 `prewarmed` 信号回主线程标记 `prewarmedAt`（仅内存，frpc 重启即失效）；XTCP 下 connect=打洞+握手完成，首条真实 SSH 不再承担打洞延时 |
| `prewarm_when_ready()` | frpc 启动后用：轮询全部 bindPort 监听就绪（200ms 间隔、8s 上限）后立即预热，替代固定 3s 等待 |
| `wait_ports_ready(ports, on_ready, on_deadline=None)` | 轮询本地端口监听就绪：全部就绪立即回调，超上限走 `on_deadline`（缺省落 on_ready） |
| `report_tunnel_issue(snk, lost)` | 感知端（remote_hub 总览刷新）上报失联/恢复，状态翻转才发 `tunnel_issue_changed` |
| `notify_tunnel_issue(snk, state)` | 失联/恢复联动：对该端口上已打开的 SSH/SFTP/RDP 面板展示/撤除提示条（`windows/remote_session/tunnel_notice`） |
| `sessions_on_port(port)` / `is_transferring_on_port(port)` | 查指定本地端口上的会话面板 / 是否有 SFTP 传输进行中 |
| `close_sessions_on_port(port, reason)` / `close_all_sessions(reason)` | 优雅关闭指定端口/全部会话面板（panel.shutdown()），返回关闭数量 |
| `shutdown()` | 停止 frpc 并关闭全局会话窗口（主窗口 closeEvent 调用） |

**P1-1 意外退出自愈**：frpc 崩溃/被回收且注册表仍有启用隧道时，按 5s→30s→2min 退避自动重启（`_on_frpc_finished` → `_schedule_recover` → `_recover_frpc`）；上次进程健康运行 ≥60s 即清零失败计数（长跑偶崩仍秒级首档恢复），三档用尽放弃到手动路径。恢复成功自动补预热。

#### 类 `FrpRemoteBridge(QObject)`

主窗口注入的远程桥接（`window()._remote_bridge`），供管理面板等子窗口委托建立会话。

---

### core.secrets

敏感配置 DPAPI 加解密（Windows 环境），其他平台自动降级为明文透传。

| 函数 | 签名 | 说明 |
|------|------|------|
| `dpapi_available()` | `() -> bool` | DPAPI 是否可用（仅 Windows） |
| `encrypt_secret(value)` | `(str) -> str` | 加密单个值，返回 `"enc:base64"`；空值/非字符串/加密失败原样返回 |
| `decrypt_secret(value)` | `(str) -> str` | 解密单个值；无 `enc:` 前缀原样返回，密文损坏（换用户/机器）返回空串 |
| `encrypt_settings(settings)` | `(dict) -> dict` | 返回敏感字段已加密的副本（不修改入参） |
| `decrypt_settings(settings)` | `(dict) -> dict` | 返回敏感字段已解密的副本（不修改入参） |
| `has_plaintext_secret(settings)` | `(dict) -> bool` | 检测是否存在未加密的敏感值（自动迁移判断用） |
| `migrate_settings_file(path=None)` | `(str?) -> bool` | **已由 `core.app_settings.migrate_legacy()` 接管**（拆分迁移时统一加密分拣）；本函数保留为兼容入口，主流程不再调用 |

敏感字段集合：顶层键（`ssh_pass` 等）+ 嵌套路径（`api_credentials.api1.password` 等）统一由 `SENSITIVE_KEYS` / `NESTED_SENSITIVE_PATHS` 维护。**2026-09-06 起**：加密落盘由 `core.app_settings` 门面在写 `config/credentials.json`、`config/database.json` 时统一执行（`ENCRYPTED_DOMAINS`），本模块的 encrypt/decrypt_settings 被门面复用。

---

### core.version

应用版本号：基于 git 分支与提交次数自动计算。

| 常量/函数 | 签名 | 说明 |
|-----------|------|------|
| `BASE_VERSION` | `str = "3.13"` | 主.次版本（手工维护，新增功能集 → 次版本 +1） |
| `APP_VERSION` | `str` | 模块级缓存完整版本号（导入时计算一次） |
| `get_branch_name()` | `() -> str` | 当前分支名；detached HEAD / 非 git 环境返回空串 |
| `get_commit_count()` | `() -> int` | 当前分支累计提交次数；失败返回 0 |
| `get_app_version()` | `() -> str` | 完整版本号 `BASE.提交数[-分支]`，如 `2.8.114` 或 `2.8.114-ai_build` |

非主分支（main/master）版本号附带分支标记；git 不可用时回退 `{BASE_VERSION}.0`，保证版本号始终可用。

---

### core.ai_providers

AI 厂商注册表：统一各厂商的 OpenAI 兼容接入参数（DeepSeek / 通义千问 / Kimi / 智谱 GLM / OpenAI GPT / Gemini）。

| 函数/常量 | 签名 | 说明 |
|-----------|------|------|
| `AI_PROVIDERS` | `tuple` | 厂商注册表：`{id, label, base_url, default_model, env_key}` |
| `DEFAULT_VENDOR` | `str = "deepseek"` | 默认厂商标识 |
| `get_provider(vendor_id)` | `(str) -> dict` | 按标识取注册信息，未知名/空值回退 DeepSeek |
| `resolve_ai_config(settings)` | `(dict) -> dict` | 解析完整 AI 调用配置 `{vendor, label, base_url, api_key, model, env_key}`；API Key 优先级：`ai_api_keys[厂商]` > 旧键 `deepseek_api_key` > 厂商环境变量 |

相关配置键：`ai_vendor`（厂商标识）、`ai_api_keys`（各厂商 Key，DPAPI 加密落盘）、`ai_model`（模型名，空用默认）、`forensic_ai_analysis`（AI 分析总开关）。

### core.theme_qss

窗口级 QSS 应用工具：主界面的 dark.qss/light.qss 过去只挂在 MainWindow，独立窗口的原生控件掉出主题；本模块把同一份主题 QSS 应用到任意窗口（含 SFTP/SSH/管理面板等 QDialog）。

| 函数 | 说明 |
|------|------|
| `apply_window_qss(window)` | 按当前 Fluent 主题加载 `styles/{dark\|light}.qss` 应用到窗口并订阅主题切换自动重应用 |
| `load_window_qss()` | 按当前主题加载窗口 QSS 文本（强调色已替换），找不到文件返回空串 |
| `substitute_accent(qss_text)` | 把 QSS 中的固定青色锚点替换为当前主题强调色 |
| `current_accent_hex()` | 当前主题强调色 hex（`qconfig.themeColor`，带容错回退） |

### core.design_tokens

设计令牌（Design Tokens）：消除 `windows/*.py` 与 `styles/*.qss` 中散落的硬编码颜色/间距/字号，统一主题色、语义色、灰阶、间距、圆角、排版刻度。纯常量、不依赖 qfluentwidgets；运行时主题色（accent）仍由 `qconfig.themeColor` 提供。

| 常量 | 说明 |
|------|------|
| `SEMANTIC` | 语义色（success/info/warning/danger/neutral 等） |
| `lighten(color, ratio)` / `darken(color, ratio)` | 颜色明暗工具（hover +10~15% / pressed -15~20%） |
| `pt_to_px(pt_size, min_px=12)` | pt→px 换算单一来源（1pt≈4/3px，最小 12px），替代 main.py / ui_mixin.py 的重复逻辑 |

### core.flow_widgets

流式工具栏共享组件：与主界面工具栏同一范式（qfluentwidgets `FlowLayout` 换行 + 高度自适应滚动容器），供主界面与售后面板/跑视频面板等复用，防止控件重叠。

| 类 | 说明 |
|------|------|
| `FlowToolbarScrollArea(QScrollArea)` | 工具栏专用滚动区域：按自身宽度计算内容高度并锁定（单行=单行高，折行=多行高，超上限滚动）；视口透明无边框 |

---

## workers/ 后台线程层

所有 Worker 均继承 `QThread`，通过 Qt 信号与 GUI 线程通信。

### workers.network_workers

SSH/SFTP/TCP 底层异步连接 Worker 集（无 UI，供 ssh_terminal / SFTPWindow / 远程面板等调用）；连接类 Worker 继承内部基类 `_BaseConnectWorker`（可重试错误自动重试 `RETRY_MAX` 次、间隔递增）。

#### 类 `TCPWorker`

TCP/SSH 连接验证工作线程。

```python
TCPWorker(host: str, port: int, username: str, password: str)
```

**信号**：`result_ready` Signal(str)——连接成功，返回 `hostname && whoami` 输出；`error` Signal(str)——连接失败，返回中文错误描述。

| 方法 | 说明 |
|------|------|
| `run()` | 执行连接（自动在 finally 中关闭 client） |
| `close()` | 手动关闭 paramiko client + transport |

---

#### 类 `SFTPConnectWorker`

异步建立 paramiko.Transport 连接（含自动重试）。

```python
SFTPConnectWorker(host: str, port: int, username: str, password: str)
```

**信号**：`connected` Signal(object)——成功，发射 paramiko.Transport 对象；`error` Signal(str)——最终失败，返回中文错误。

| 方法 | 说明 |
|------|------|
| `abort()` | 请求中止重试循环 |

---

#### 类 `SFTPListWorker`

异步 SFTP 列目录（使用 `listdir_attr` 单次网络往返）。

```python
SFTPListWorker(transport: paramiko.Transport, remote_path: str)
```

**信号**：`result` Signal(str, list)——(路径, 条目列表)，条目为 dict：`{name, is_dir, size, mtime, perm}`；`error` Signal(str)——列目录失败。

---

#### 类 `SFTPOperationWorker`

异步 SFTP 文件操作（上传/下载/删除/创建目录/重命名/创建文件），支持进度、暂停、取消。

```python
SFTPOperationWorker(conn_params: tuple, operation: str,
                    local_path='', remote_path='', file_size=0)
```

**operation 取值**：`'upload'` | `'download'` | `'delete'` | `'rmdir'` | `'mkdir'` | `'rename'` | `'create_file'`

**信号**：`success` Signal(str)——操作成功消息；`error` Signal(str)——操作失败消息；`progress` Signal(int, int)——(已传输字节, 总字节)。

| 方法 | 说明 |
|------|------|
| `pause()` | 暂停传输 |
| `resume()` | 恢复传输 |
| `stop()` | 取消传输（抛出 InterruptedError） |

---

#### 类 `SFTPDirTransferWorker`

异步 SFTP 目录递归传输（整目录上传/下载）。

```python
SFTPDirTransferWorker(conn_params: tuple, operation: str,
                      local_dir='', remote_dir='', dir_name='')
```

**operation 取值**：`'upload_dir'` | `'download_dir'`

**信号**：`success` Signal(str)——传输完成（含文件数统计）；`error` Signal(str)——传输失败/部分失败；`progress` Signal(int, int)——(已传输字节, 总字节)。

| 方法 | 说明 |
|------|------|
| `pause()` / `resume()` / `stop()` | 传输控制 |

---

#### 类 `SSHConnectWorker`

异步建立 SSH 连接（保持 client 存活，含自动重试）。

```python
SSHConnectWorker(host: str, port: int, username: str, password: str)
```

**信号**：`connected` Signal(object)——成功，发射 paramiko.SSHClient 对象；`error` Signal(str)——最终失败。

| 方法 | 说明 |
|------|------|
| `abort()` | 请求中止重试循环 |

---

#### 类 `SSHExecWorker`

异步执行 SSH 命令（exec_command 模式，无持久 shell）。

```python
SSHExecWorker(client: paramiko.SSHClient, command: str)
```

**信号**：`output` Signal(str)——标准输出内容；`error` Signal(str)——标准错误 / 异常信息；`done` Signal()——命令执行完毕。

---

## workers/table_worker.py 球桌/设备数据 Worker

球桌与设备数据 API 异步请求 Worker（均继承 `QThread`），账号密码统一从配置门面 `core.app_settings` 的 `api_credentials` 键读取（credentials 域，DPAPI 加密落盘）。

#### 模块级函数与常量

| 函数/常量 | 签名 | 说明 |
|-----------|------|------|
| `_load_api_credentials()` | `() -> dict` | 读取 `settings.json` 的 `api_credentials` 配置 |
| `get_active_api_source()` | `() -> str` | 当前启用的设备数据源（`'kd'`/`'xqzg'`，默认 kd） |
| `build_image_path(file_path, device_code, category)` | `(str, str, str) -> str` | 构造迁移路径 `media/{日期}/{设备码}/{分类目录}/` |
| `CATEGORY_DIRS` | `dict` | 中文分类 → 服务器目录名（正常=normal / 操作=except / 待处理=pending / 使用=operation / 精度=accuracy / 问题=already / 废弃=rubbish） |
| `DIR_CATEGORIES` | `dict` | `CATEGORY_DIRS` 的反向映射 |

#### 类 `TableFetchWorker`

拉取球桌列表（wechat2-billiard.newbv.cn，无认证，pageSize=1000 一次拉完写入本地库）。

```python
TableFetchWorker()
```

**信号**：`result_ready` Signal(list)——全量球桌数据列表；`error` Signal(str)——错误信息。

#### 类 `SnookerOmFetchWorker`

拉取接口1（xqzg.newbv.cn）设备状态数据，Session + CSRF 认证，401/403 自动重登录重试一次。响应数据在 `results` 键。

```python
SnookerOmFetchWorker(file_path="", page=1, pagesize=1000,
                     username=None, password=None)
```

**信号**：`result_ready` Signal(dict)——完整 JSON（含 total / results / summary_row）；`error` Signal(str)——错误信息。

#### 类 `DevicesFetchWorker`

拉取接口2（kd.newbv.cn:30005）设备状态数据，JWT Bearer Token 认证（登录端点 `/api/getAccessToken/`），401 自动重登录重试一次。响应数据在 `lists` 键，`file_path` 参数为日期分区（如 `2026/08/02`）。

```python
DevicesFetchWorker(file_path="", page=1, pagesize=1200,
                   username=None, password=None)
```

**信号**：`result_ready` Signal(dict)——完整 JSON（含 lists）；`error` Signal(str)——错误信息。

#### 类 `MigrateImageWorker`

异步执行图像分类迁移，按数据源分派端点与认证（2026-08-22 修复：此前误调 kd 端点导致 xqzg 假成功）：

- **kd**：JWT + `POST /api/devices/migrate_image/`（接口2）
- **xqzg**：Session + `Referer` + `X-CSRFToken` + `POST /api/snooker_om/migrate_image/`（接口1，Django HTTPS CSRF 校验需带 Referer）
- **成功判定**：HTTP 200 且响应体 `status != "error"`（xqzg 用 200+`{"status":"error"}` 表达业务失败，只看状态码会假成功）
- 401/403（含 xqzg Session/CSRF 过期）自动重登重试一次，支持批量

```python
MigrateImageWorker(file_path, device_code, file_names,
                   src_category, dest_category,
                   username=None, password=None, source="kd")
```

- `file_path`：日期路径，如 `"2026/08/02"`
- `src_category` / `dest_category`：中文分类名（见 `CATEGORY_DIRS`）
- `source`：`"kd"`（默认）/ `"xqzg"`，决定端点与认证方式；面板调用时传 `self._active_source()`

**信号**：`success` Signal(int)——成功迁移的图片数量；`error` Signal(str)——错误信息（含失败文件列表摘要）；`progress` Signal(int, int)——(当前进度, 总数)。

#### 类 `LoginTestWorker`

测试 API 登录是否可用（管理设置页「测试连接」按钮）。

```python
LoginTestWorker(api_name, username=None, password=None)
# api_name: "api1"（xqzg）或 "api2"（kd）
```

**信号**：`success` Signal(str)——成功提示；`error` Signal(str)——失败原因。

#### 类 `HealthUpdateWorker`

异步重置设备健康度（健康度告警面板「一键归零」）：逐台 POST xqzg `/api/snooker_om/update_health/` 把服务端健康度写为 4000（接口默认值，等于清零告警）。Session + CSRF 认证，401/403 自动重登重试一次。**成功判定**：HTTP 200 且响应体 `code == 200`（只看状态码会假成功）。

```python
HealthUpdateWorker(items, username=None, password=None)
# items: [(球桌名, device_code), ...]
```

**信号**：`result_ready` Signal(list, list)——(成功球桌名列表, 失败列表 [(球桌名, 失败描述), …])；`error` Signal(str)——账号未配置 / 登录失败等整体错误。

#### 类 `TodeskToggleWorker`

ToDesk 远程开关 Worker（球桌管理 todesk 开关列数据源）：登录 → `csrftoken` → form POST `value/`（datacode=**设备编码**）→ 轮询 `status/?keyword=桌号` 确认。权威显示口径=服务端 value/ 收到即记录的 `todesk_action`（'20' 开 / '80' 关）；`todesk_status` 依赖设备上报不可信，仅作兜底。

```python
TodeskToggleWorker(device_code, table_id, turn_on, parent=None)
# turn_on: True（开，datavalue=20）/ False（关，datavalue=80）；凭据自动从 api1 读取
```

**信号**：`sent_ok`——指令已被服务端受理；`confirmed` 桌号——轮询确认开关已生效；`unconfirmed` 桌号——指令已下发但轮询未确认（以 todesk_action 口径显示）；`error` str——登录失败/网络异常。

> ⚠️ `value/` 三坑：必须 form 提交（JSON 返回 415）；errorcode 嵌在 data 里；datacode 传 todesk_id 会报「设备号没找到」。

#### 模块级函数（todesk 辅助）

| 函数 | 签名 | 说明 |
|------|------|------|
| `parse_value_response(data)` | `(dict) -> tuple` | 解析 value/ 响应，返回 (ok, errtext)——⚠️ errorcode 嵌在 data 里，只看顶层会假成功 |
| `pick_todesk_status(rows, table_id, device_code)` | `(list, str, str) -> str?` | 从 status/ 返回行中挑出目标设备的 todesk_status 原始值（未命中 None） |

#### 迁移说明：SingleVideoWorker → workers/single_video_worker.py

> 2026-09 起迁移至独立模块 [workers/single_video_worker.py](#workerssingle_video_workerpy-单杆视频生成-worker)（信号签名不变）。

单杆视频生成工作线程（工具菜单「单杆视频」）。日志解析（帧级计分提取）、视频水印合成均在子线程执行，逐行进度通过信号回传。

```python
SingleVideoWorker(params: dict)
```

**信号**：`line` Signal(str)——处理进度日志（追加到对话框输出区）；`finished_ok` Signal(str)——生成成功，返回视频路径；`error` Signal(str)——生成失败，返回错误首行。

---

## workers/collect_worker.py 收集与上传 Worker

设备文件收集（视频/日志/CPP 日志/detect.bin）与打包上传后台线程。

#### 模块级函数

| 函数 | 签名 | 说明 |
|------|------|------|
| `clip_base_name(fname)` | `(str) -> str` | 截取文件名 `kd` 之前的部分作为基础名（视频/日志同名关联） |
| `date_from_base(base)` | `(str) -> str` | 从基础名提取日期（`20260724_225031` → `2026-07-24`），无法解析返回空串 |
| `norm_device_suffix(name)` | `(str) -> str` | 设备后缀归一化：只留数字并去前导零（S8/08/TV2 → 8/8/2） |
| `fuzzy_match_device_dir(videos_dir, candidates)` | `(str, list) -> tuple` | 模糊搜索本地设备目录（命名与球桌号不一致时兜底）：店号前缀相同 + 后缀归一化匹配 |
| `resolve_device_dir(videos_dir, candidates)` | `(str, list) -> tuple` | 收集入口的设备目录三级解析（C4）：精确目录 → 球桌号变化匹配 → 模糊搜索 |

#### 类 `FileCopyWorker`

异步文件拷贝（`shutil.copy2` 的线程替代）。

```python
FileCopyWorker(src, dst)
```

**信号**：`copy_finished`——拷贝成功完成；`error` Signal(str)——拷贝失败。

#### 类 `CollectFilesWorker`

异步收集设备视频/日志/CPP 日志/detect.bin 到 `upload` 工作区。已存在的目标文件直接跳过（重复点击不重复复制）。

```python
CollectFilesWorker(videos_dir, device_id, base_names)
```

**信号**：`done` Signal(str, int, list)——(设备目录名, 实际复制文件数, 缺失项说明列表)；`error` Signal(str)——错误信息。

#### 类 `ZipUploadWorker`

打包 upload 目录为 zip → SFTP 上传 → 清空本地 upload 目录。凭据用上传专用字段（不复用 SSH 凭据），支持取消（取消后自动清理临时 zip）。

```python
ZipUploadWorker(upload_root, host, port, username, password,
                remote_dir, parent=None, content_root=None,
                zip_prefix="upload", zip_dir=None, remove_zip_after_done=False)
```

**信号**：`progress` Signal(str)——阶段提示（打包中/连接中/上传中）；`percent` Signal(int)——上传字节进度 0-100；`done` Signal(str)——成功信息（zip 名与远端路径）；`error` Signal(str)——错误信息；`cancelled`——用户取消完成（临时 zip 已清理）。

---

## workers/newlog_worker.py 批量整理 Worker

#### 类 `NewLogWorker`

后台运行 NewLog 批量整理主流程（按 Excel 署名筛选，批量归类视频/日志/配置文件）。

```python
NewLogWorker(target_name)
```

**信号**：`line` Signal(str)——逐行运行日志（临时 Handler 转发 NewLog 模块 logger 输出）；`finished_ok` Signal(str)——整理完成，返回输出目录；`error` Signal(str)——运行失败。

---

## workers/aftersale_worker.py 售后数据 Worker

#### 类 `AftersaleDBWorker`

通用后台 DB 操作 Worker：把任意同步 DB 函数（`aftersale_db` / `ledger_db` / `table_db` 等）移到工作线程，避免阻塞 GUI。类名带 aftersale 前缀是历史遗留，实际售后、跑视频、运维面板均在使用。

```python
AftersaleDBWorker(func, *args, **kwargs)
```

**信号**：`result_ready` Signal(object)——查询/保存结果（**不能命名为 finished，会遮蔽 Qt 原生 finished**）；`error` Signal(str)——异常信息 `类型名: 描述`。

**保活与清理机制**（防频繁刷新时旧 worker 被 GC 销毁导致 `QThread: Destroyed while thread is still running` 崩溃）：
- 模块级 `_running` 集合强引用，线程退出前不被 GC
- 用 Qt **原生** `finished` 信号挂 `_release`（run() 返回后由 Qt 发射，线程已标记结束，销毁安全）
- `run()` 内用 `isInterruptionRequested` 丢弃过期结果（新查询取代旧查询时不回调）

> 坑：PySide6 中 super().finished 会被子类同名信号遮蔽，不能用于此目的；自定义信号必须避开 QThread 原生信号名（finished/started）。

---

## workers/mysql_sync_worker.py MySQL 连接测试 Worker

镜像推送 Worker（MysqlSyncWorker）已随机制 B 下线（2026-08-23），本模块只保留连接测试。

#### 类 `MysqlTestWorker`

异步测试 MySQL 连接。

```python
MysqlTestWorker(cfg, parent=None)
```

**信号**：`finished` Signal(bool, str)——(是否成功, 描述)。

## workers/backup_worker.py 周备份 Worker

异步执行 `fallback_backup.maybe_backup`（MySQL → SQLite 兜底基线刷新），避免阻塞 UI。

#### 类 `BackupWorker`

**信号**：`progress` Signal(str)——阶段进度；`result` Signal(bool, str, int)——完成 (ok, message, count)。

## workers/cleanup_worker.py 数据保留清理 Worker

异步执行 `data_retention.run_cleanup`（过期分区删除 + 按大小清理），避免阻塞 UI。由主窗口 `_init_data_retention` 挂载：启动延迟 8s 首次检查 + 每 24h 周期执行，仅在确有删除时提示。

#### 类 `CleanupWorker`

**信号**：`progress` Signal(str)——阶段进度（各表删除行数）；`result` Signal(bool, str, int)——完成 (ok, message, deleted_count)。

## workers/merge_back_worker.py 合并回写 Worker

异步执行 `merge_back.merge_back`（MySQL 恢复后 LWW 合并兜底增量），避免阻塞 `_get_conn` 调用方。

#### 类 `MergeBackWorker`

**信号**：`progress` Signal(str)——阶段进度；`result` Signal(bool, str, int)——完成 (ok, message, count)。

worker 可能从非主线程的 `_trigger_merge_back` 创建：result 通过 `QApplication` 顶层窗口找 MainWindow 弹 InfoBar，找不到则降级 conn_logger 落盘。

## workers/network_workers.py 网络连接 Worker 集

SSH/SFTP/TCP 底层异步连接 Worker（无 UI，供 ssh_terminal / SFTPWindow / 远程面板等调用）。类清单与信号签名见上文 [workers.network_workers](#workersnetwork_workers) 一节，此处不再重复。

## workers/single_video_worker.py 单杆视频生成 Worker

#### 类 `SingleVideoWorker`

后台执行单杆视频生成（工具菜单「单杆视频」；单杆模块自 table_json 收编后由本模块承载）。日志解析（帧级计分提取）、视频水印合成均在子线程执行，模块级 logger（`SingleShotVideo`）经 `_LineSignalHandler` 逐行转发为信号回传。

```python
SingleVideoWorker(params: dict, parent=None)
```

**信号**：`line` Signal(str)——处理进度日志（追加到对话框输出区）；`finished_ok` Signal(str)——生成成功，返回视频路径；`error` Signal(str)——生成失败，返回错误首行。

## workers/update_worker.py 自动更新 Worker

自动更新的两段后台线程（检查 / 下载解压）；纯逻辑在 `core/updater.py`（无 Qt 依赖），本模块只做线程与信号适配，均不阻塞 UI。

#### 类 `UpdateCheckWorker`

检查更新：拉 latest.json 比对本地版本。

```python
UpdateCheckWorker(base_url, local_version, channel=CHANNEL_AUTOWORK)
```

**信号**：`found` dict——发现新版本（latest 条目，含 `_resolved_url`）；`up_to_date` str——已是最新（当前版本号）；`error` str——网络/解析错误。

#### 类 `UpdateDownloadWorker`

下载 + 校验 + 解压 staging（不执行安装；安装由主程序退出后外部 updater 完成）。

```python
UpdateDownloadWorker(base_url, entry, app_dir, main_exe, local_version)
```

**信号**：`stage` str——阶段切换（下载/解压）；`progress` (int, int)——(已完成字节, 总字节)；`ready` dict——staging 就绪（`prepare_update` 返回值：mode/staging_dir/zip_path/files_count）；`error` str——校验失败/网络错误；`cancelled`——用户取消完成。

---

## database/ 数据层

### database.table_db

SQLite3 本地数据层（`database/tables.db`），线程内共享连接。

#### 球桌数据（wechat2-billiard）

| 函数 | 说明 |
|------|------|
| `parse_snk_code(remark)` | 从 remark 正则提取 snk 标识（如 `snk_001`），无则返回空串 |
| `save_all(rows)` | 全量覆盖写入球桌表，返回条数（自动解析 snk_code；remark 无 snk 时保留旧库手动值） |
| `query_page(page_no, page_size, keyword="", include_test=True, include_manual=True)` | 分页查询，返回 `(total, rows)`；`include_test` 排除「公司测试」球房、`include_manual` 排除手动版本设备（name/roomName 含 `@s`） |
| `insert_one(record)` | 手动插入一条记录（API 失效时的兜底入口） |
| `update_snk_by_name(name, snk_code)` | 按球桌号手动写入/修改 snk（TRIM 匹配），空串表示清空 |
| `get_snk_by_name(name)` | 按球桌号查 snk（设备状态页 table_id ↔ 球桌管理 name 关联） |

球桌表字段（`FIELDS`）：`name`、`roomName`、`onlineStatusName`、`remark`、`cameraPassExt`、`snk_code`（SNK 标识，手动维护）、`code`（设备编码，接口同步）。旧库自动惰性迁移：首次连接时 `ALTER TABLE ADD COLUMN` 补列并重建 FTS 索引，数据无损。

**全文搜索（FTS5）**：三张表均建 trigram 虚拟表 + 触发器增量同步（external content 模式）；关键词 ≥3 字符走 FTS 子串匹配，短关键词或 SQLite 缺 FTS5 支持时自动回退多列 `LIKE`。排序列名经白名单校验，数值字段（TEXT 存储）自动 `CAST AS REAL` 防字典序错误。

#### 健康度告警（health_alerts）

| 函数/常量 | 说明 |
|------|------|
| `HEALTH_WARN = 4000.0` / `HEALTH_SEVERE = 5000.0` / `HEALTH_INVALID_MAX = 400000.0` | 阈值：4000 为接口默认值视为空值；4000~5000 异常；>5000 严重异常；>40 万脏数据 |
| `sync_health_alerts(rows)` | 按最新球桌数据同步告警表，返回当前应展示条数（排除默认值/脏数据/公司测试；已处理且 health 变化时清除标记重新展示；消失设备清理） |
| `query_health_alerts()` | 查询未处理告警，按需排序：空闲且严重异常 > 健康度异常 > 其余严重异常，同级按 health 降序 |
| `mark_health_alerts_resolved(names)` | 标记告警为已处理（记录当时 health 值），返回受影响行数 |

#### 设备状态数据（xqzg / kd）

| 函数 | 说明 |
|------|------|
| `save_xqzg(rows, file_path="")` / `query_xqzg_page(page_no, page_size, keyword="", file_path="", order_by="", desc=False, include_files=False)` | xqzg 数据按日期分区存取（2026-08-22 起与 kd 同构，按 `file_path` 分区；`include_files=True` 时返回 8 类文件清单反序列化，列表页默认轻量模式） |
| `save_kd(rows, file_path="")` / `query_kd_page(page_no, page_size, keyword="", file_path="", order_by="", desc=False, include_files=False)` | kd 数据按日期分区存取（自动序列化/反序列化文件列表字段）；`include_files=False` 时轻量查询不含 8 类文件 JSON |
| `upsert_kd(rows, file_path="")` | 按 `(file_path, device_code)` 增量更新/插入（keyword 搜索拉取专用，不覆盖同日期其他设备） |
| `get_kd_row_full(row_id)` / `get_xqzg_row_full(row_id)` | 按 id 查完整行（含文件清单反序列化，配合轻量列表页懒加载） |
| `get_kd_dates()` / `get_xqzg_dates()` / `get_xqzg_synced_dates()` | 本地已有的日期分区列表（降序）/ xqzg 本地分区 / xqzg 已同步分区 |

kd_status 历史分区保留 60 天（`_KD_KEEP_DAYS`），每次保存后自动清理过期分区；手动/配置化清理入口 `prune_kd_history(keep_days=60)`（数据保留清理 Worker 兜底执行），返回删除条数。

#### ToDesk 开关状态（球桌管理 todesk 列）

| 函数 | 说明 |
|------|------|
| `get_todesk_ids(codes)` | 按设备编码批量取最新非空 ToDesk 号与开关状态（球桌管理 todesk 列数据源） |
| `parse_todesk_id(item)` | 从 xqzg status 行提取 ToDesk 号 |
| `parse_sunflower_id(item)` | 从 remark 文本提取向日葵识别码（球桌管理「向日葵」列数据源） |
| `update_todesk_status(device_code, todesk_status)` | 开关指令确认后回写该设备最新分区行（返回受影响行数） |

#### 跨面板联动查询（球房 ↔ 球桌 ↔ 设备）

| 函数 | 说明 |
|------|------|
| `parse_city(item)` | 从接口记录解析城市（字段 `roomCity`，容错大小写/别名），售后面板球房带出地区用 |
| `query_tables_by_room(room_kw, limit=30)` | 按球房名模糊查询球桌列表（售后面板：输入球房带出桌号/SNK/地区候选） |
| `get_table_name_by_snk(snk)` | 按 snk 标识反查球桌号（隧道面板「关联球桌」展示用） |
| `get_table_info_by_snk_or_host(snk="", host_hint="")` | 按 snk 或 host 反查球桌信息 dict（取证报告「关联球桌」用；双后端安全 API） |
| `get_meta()` | 返回球桌表 `(总条数, 最后同步时间字符串)`，无数据时 `(0, "")` |
| `close()` | 关闭数据库连接（应用退出时调用） |

#### 设备状态扩展查询与统计（kd_status）

| 函数 | 说明 |
|------|------|
| `get_kd_synced_dates()` | 从 sync_meta 提取曾同步过的 kd 日期（含接口返回空数据的日期，与本地分区 `get_kd_dates()` 区分） |
| `get_latest_kd_status(table_id)` | 查指定球桌最近一次上报的设备状态（轻量单条 SQL，远程连接前置检查用） |
| `get_latest_kd_status_by_code(device_code)` | 按设备码模糊匹配最新分区设备状态（球桌面板离线前置检查降级用） |
| `query_latest_kd_full(table_id="", device_code="")` | 查指定球桌/设备码最新分区的完整 kd 行（含文件清单，取证报告用） |
| `query_kd_by_device(device_code, file_path="")` | 按 device_code 精确查询单台设备完整信息（缺省最新分区） |
| `find_kd_file_status(device_code, date, clip_base)` | 按 设备码+日期分区+文件基础名 反查所属分类（C6 文件归类迁移用） |
| `query_kd_trend(device_code, days=30)` | 单设备近 N 天按日期的指标序列（单条 SQL，趋势折线图数据源） |
| `query_kd_ranking(date="", top=10, by="error_rate")` | 指定日期设备指标 TOP N 排行（排序字段白名单校验） |
| `query_kd_alerts(days=7)` | 突增预警：最新分区 error_rate > 前 N 日均值×2 的设备（单条 CTE SQL） |

#### 提交台账（submission_log）

| 函数 | 说明 |
|------|------|
| `log_submission(device_code="", table_id="", club_name="", category="", file_name="", file_path_date="", collect_ok=False)` | 写入一条精度/问题提交台账，返回新记录 id |
| `update_submission_collect(log_id, ok)` | 回填收集结果（collect_ok），log_id 无效返回 0 |
| `update_submission_upload(upload_zip, ok, within_hours=24)` | 回填上传结果：打包上传是整目录 zip（多设备合并），按时间窗匹配当日未上传记录批量回填 |
| `get_submission_stats(device_code=None, days=30)` | 近 N 天提交次数聚合（单条 GROUP BY，列表页批量匹配无 N+1） |

#### 设备映射（device_mapping）

设备码 → 本地目录映射（收集/上传入口按映射定位文件，替代历史硬编码）。

| 函数 | 说明 |
|------|------|
| `get_device_mapping(device_code)` | 按设备码查映射，返回 dict（无记录返回空 dict） |
| `set_device_mapping(device_code, local_dir, source='auto')` | 写入/更新映射（source: auto=自动发现 / manual=手动指定） |
| `get_all_device_mappings()` | 全部映射 `{device_code: local_dir}`（收集入口批量预取） |
| `delete_device_mapping(device_code)` | 删除指定映射（清除错误映射入口），返回受影响行数 |

**两数据源字段对照（同套接口字段，无独有字段）**：

接口1（xqzg，`https://xqzg.newbv.cn/api/snooker_om/status/`）与接口2（kd，`http://kd.newbv.cn:30005/api/devices/status/`）返回同套字段集（约 50 个）：`device_code` / `room_id` / `table_id` / `club_name` / `status` / `address` / `local_code` / 各类计数 / `target_directory` / `normal_total` / `pic_total` / 8 类文件清单（`normal_files`…`version_files`）/ `region` / `error_rate` / `operation_rate` 等。`xqzg_status` 与 `kd_status` 表均按「13 个统计字段（`STATUS_FIELDS`）+ 10 个扩展字段（`KD_EXTRA_FIELDS`：`device_code`、`target_directory`、`status`、8 类文件清单）」落库，文件清单 JSON 序列化；FTS 搜索字段 = 统计字段 + `device_code`。

**设计上就不同的点（非 bug，勿按缺陷处理）**：

| 差异 | kd（接口2） | xqzg（接口1） |
|------|------|------|
| 数据组织 | 按日期分区快照（`file_path`=`2026/08/02`），历史保留 60 天 | 按日期分区快照（`file_path`，2026-08-22 起与 kd 同构），面板日期选择器可用 |
| `target_directory` 路径 | `/home/opt/backend/media/{日期}/{device_code}`（含日期分区） | `/opt/rbac-SnookerOm/backend/media//{device_code}`（无日期、双斜杠） |
| 图片迁移（migrate_image） | 可用（JWT + `POST /api/devices/migrate_image/`） | 可用（Session + Referer + X-CSRFToken + `POST /api/snooker_om/migrate_image/`，2026-08-22 修复：此前误调 kd 端点导致假成功——xqzg 文件从未被移动） |
| 服务端关键词过滤 | 支持（`DevicesFetchWorker(keyword=...)`） | 不支持（全量拉取后本地 FTS 过滤） |
| 每小时定时刷新 | 启用（status 时效性高） | 停用 |
| 响应键名 | `lists` | `results` |
| 认证 | JWT（`/api/getAccessToken/`），401 自动重登 | Session + CSRF，401/403 自动重登 |
| `photo_list` | 有内容 | 恒为空数组 |

**修复记录（2026-08）**：历史版本 `save_xqzg` 只落库 13 个统计字段，丢弃 `device_code`/`status`/`target_directory`/8 类文件清单，导致 xqzg 源下状态列恒为「未知」、文件面板/右键复制为空、CSV 缺设备编码列、status 列排序 SQL 报错（旧表无该列）且 error 信号未连接静默失败。已修复：`save_xqzg` 全字段落库；旧库（SQLite `PRAGMA` / MySQL `SHOW COLUMNS`）启动时自动 `ALTER TABLE ADD COLUMN` 补列并重建 FTS（`xqzg_fts` 补 `device_code` 列后 drop 重建）；查询/排序/FTS/懒加载全覆盖扩展字段；两个拉取 Worker 按 `total` 自动翻页拉全（单页 1000/1200，上限 50 页），超过单页大小的设备不再静默丢失；`_DBQueryWorker` 信号改名 `result_ready`（避免遮蔽 Qt 原生 `finished`）并补齐 error 连接，切换数据源来回切换不报错、不丢缓存。

**MySQL 迁移二坑（2026-08 二次修复）**：(1) MySQL 的 TEXT/BLOB 列不允许字面量 `DEFAULT` 子句（`ALTER ... ADD COLUMN normal_files LONGTEXT DEFAULT '[]'` 报 1101），迁移循环在第一个文件列中断且异常被吞 → 标量列补上、8 个文件列永久缺失（点文件列报 1054 Unknown column）。迁移改为**逐列检测**（缺哪列补哪列），文件列 `LONGTEXT` 不带 DEFAULT，读取端 `json.loads(None)` 兼容 NULL。(2) `save_xqzg` 是 DELETE 全表 + INSERT，若 INSERT 因缺列失败会把整表数据清空（MySQL autocommit 不可回滚）→ 列表 total=0、翻页按钮禁用。写入前新增列探测 `_probe_status_ext_cols`：缺扩展列时先报错不删数据；落库 Worker 补连 error 信号，失败弹「保存失败」而非静默。**注意**：修复前同步的旧行扩展列（device_code/status/文件清单）为 NULL/空，需在 xqzg 源下重新点一次「搜索」拉取全字段数据后，状态列/文件面板才完整。

---

### database.backend

数据库后端切换层（测试模式：MySQL 完全替代本地 SQLite）。`table_db` / `aftersale_db` 的所有读写均经此模块路由：开关关闭走 sqlite3 本地连接（原行为零改动），开关开启走 `MysqlConnectionAdapter` 包装的 pymysql 连接，调用方无需感知方言差异。

| 函数/常量 | 签名 | 说明 |
|-----------|------|------|
| `is_mysql_test_mode()` | `() -> bool` | MySQL 测试模式是否开启（读 `config/database.json` → `mysql_sync.enabled`；热路径函数，配置进程内缓存） |
| `invalidate_mysql_settings_cache()` | `()` | 使 MySQL 配置缓存与已有线程连接失效（保存 MySQL 配置后必须调用，generation 递增驱动各线程重建连接） |
| `mysql_settings_generation()` | `() -> int` | 当前配置代次，连接层据此判断是否需重建连接 |
| `get_state()` | `() -> str` | 当前后端状态：`ONLINE`=MySQL 主库 / `DEGRADED`=SQLite 兜底（降级期间本地写入，恢复后由 merge_back 合并） |
| `mark_degraded()` / `mark_online()` | `() -> bool` | 标记降级/恢复，返回是否发生状态切换（切换时触发合并回写/状态提示） |
| `create_mysql_connection()` | `() -> MysqlConnectionAdapter` | 创建 MySQL 连接适配器；pymysql 未安装抛 RuntimeError。关键参数：`autocommit=True`（QThread 结束后 thread-local 连接被丢弃，若留未提交事务会持元数据锁级联卡死）、读写超时 60s |
| `convert_placeholders(sql)` | `(str) -> str` | SQLite 占位符 `?` → MySQL `%s`（跳过字符串字面量内的 `?`） |
| `convert_on_conflict(sql)` | `(str) -> str` | `ON CONFLICT(col) DO UPDATE SET ...=excluded.x` → `ON DUPLICATE KEY UPDATE ...=VALUES(x)` |
| `convert_insert_or_replace(sql)` | `(str) -> str` | `INSERT OR REPLACE` → `INSERT`（MySQL 无此语法） |
| `escape_literal_percent(sql)` | `(str) -> str` | 字符串字面量内的单个 `%` → `%%`（pymysql 参数化执行所需） |
| `MYSQL_DDL` | `dict` | 8 张表的 MySQL 建表语句（IF NOT EXISTS 幂等，与 SQLite DDL 一一对应） |

#### 类 `MysqlConnectionAdapter`

模拟 `sqlite3.Connection` 接口。`execute` / `executemany` 自动套用全部方言转换；`PRAGMA` 静默跳过；`executescript` 按分号拆条执行。

| 方法 | 说明 |
|------|------|
| `healthy()` | 连接是否仍可复用；一次连接级错误后由 table_db 重建 |
| `begin()` | 显式开启原子批量写事务（连接默认仍使用 autocommit） |
| `column_exists(table, column)` / `table_exists(table)` | 检查列/表是否存在（替代 SQLite PRAGMA table_info） |
| `commit()` / `rollback()` / `close()` | 事务与连接管理 |

#### 类 `MysqlCursorAdapter`

模拟 `sqlite3.Cursor` 接口（可迭代）。

| 方法 | 说明 |
|------|------|
| `execute(sql, params)` / `executemany(sql, seq_params)` | 执行 SQL（方言转换经所属 ConnectionAdapter） |
| `fetchone()` / `fetchall()` | 取结果行 |
| `description` / `rowcount` / `lastrowid` | 游标元数据（属性语义） |
| `close()` | 关闭游标 |

方言转换还涵盖：SQLite `date` 函数 → `DATE_SUB/DATE_FORMAT`、去除 `COLLATE NOCASE`、`sync_meta.key/value` 保留字加反引号。

---

### database.aftersale_db

售后记录数据层（SQLite / MySQL 双后端，自动跟随 MySQL 测试开关）。连接复用 `table_db` 双后端路由（SQLite 单连接 / MySQL thread-local）；MySQL 模式下多人各自提交即提交即落库，其他用户刷新/手动同步后可见。

#### 常量

| 常量 | 说明 |
|------|------|
| `ISSUE_TYPES` | 类型枚举（11 值：硬件问题/程序相关/识别问题/...） |
| `REGIONS_PRESET` | 地区预置（9 值，允许自由输入新地区） |
| `RESPONSE_TIME_PRESET` | 响应时间预置档位（5 档，允许自由输入） |
| `RECORD_FIELDS` | 记录字段元组（与建表 DDL 一致，不含 id） |

#### 面板设置联动（记住日期 / 自动刷新，config/aftersale.json）

| 函数 | 说明 |
|------|------|
| `remember_occurred_enabled()` | 「记住上次发生日期」开关（缺省开启） |
| `load_last_occurred()` / `save_last_occurred(occurred)` | 上次新增记录的发生日期（yyyy-MM-dd）读取/记忆（开关关闭或空值时不写） |
| `set_remember_occurred(enabled)` | 写「记住上次发生日期」开关（统一设置-面板设置-售后 联动入口） |
| `auto_refresh_enabled()` / `set_auto_refresh(enabled)` | 「自动刷新记录」开关（缺省关闭；统一设置-面板设置-售后 联动入口） |
| `auto_refresh_interval()` / `set_auto_refresh_interval(seconds)` | 自动刷新间隔秒数（缺省 30；仅接受 ≥5 的整数，防误配高频轮询） |
| `change_fingerprint()` | 全表轻量指纹 (条数, 最大 updated_at, 最大 id)——记录页自动刷新判据，指纹不变跳过重查 |

#### 周期计算（可配置模式 + 物化列）

周期模式：`tue`=周二起（默认）/ `mon`=自然周（周一起）/ `custom`=自定义起始日+周期天数 / `month`=自然月。配置经 `core.app_settings` 门面持久化到 `config/aftersale.json` 的 `aftersale_cycle` 键。

| 函数 | 签名 | 说明 |
|------|------|------|
| `load_cycle_mode()` | `() -> dict` | 读取周期模式 `{type, start, span}`，缺省/非法回退周二起。**进程内缓存**（`_cycle_mode_cache`），`save_cycle_mode` 成功后失效重读；外部手改配置文件需 `app_settings.invalidate_cache()` |
| `save_cycle_mode(mode)` | `(dict) -> dict` | 经门面合并写（域内其余键不动），成功后失效缓存并置重算待办标志 `aftersale_cycle_recalc_pending=true`，返回规范化配置 |
| `cycle_span_days()` | `() -> int` | 当前模式周期天数：tue/mon 固定 7，custom 取配置值（≥1），month 取当月实际天数 |
| `cycle_start_of(dt)` | `(datetime) -> str` | 计算给定时间所属周期起始日（`yyyy/MM/dd`），按当前模式分发 |
| `current_cycle_start()` | `() -> str` | 当前周期起始日 |
| `cycle_label(cycle_start)` | `(str) -> str` | 周期展示标签：周模式 `08/18 - 08/24`；month 模式 `2026-08` |
| `cycle_date_range(cycle_start)` | `(str) -> (date, date)` | 周期起止日期：month 模式为整月，其余为起始日 + span-1 天 |

**周期归属 = 物化列等值过滤（2026-09-06 S3 优化，替代旧 Python 侧动态计算）**：`aftersale_records.cycle_start` 物化列在写入时按当时周期口径落库（`insert_record`/`update_record`/导入均维护），查询侧 `cycle_start = ?` 等值过滤直接走 `idx_aftersale_cycle` 覆盖索引（24.2ms → 0.9ms）。周期口径变更（`save_cycle_mode`）后触发全表重算 `recalc_cycle_starts`（分批幂等）；重算失败自愈：`aftersale_cycle_recalc_pending` 标志残留，下次面板首载 `recalc_cycle_starts_on_load` 全量追平。存量空值行由 `_ensure_cycle_materialized` 在首次周期筛选时自动兜底回填。

> 旧版「周期在 Python 侧按 occurred_at 动态归属、SQL 不落库」方案已整体替代；occurred_at 缺失/非法的记录归属空串（不进任何周期），与旧口径一致。

#### 增删改

| 函数 | 签名 | 说明 |
|------|------|------|
| `insert_record(record)` | `(dict) -> int` | 新增记录返回 id。created_at 取填写时刻；occurred_at 缺省取当日；cycle_start 按发生时间归属物化；snk_code/device_code 未提供时按桌号精确匹配球桌库自动带出；成功后失效动态候选缓存 |
| `update_record(record)` | `(dict) -> int` | 按 id 更新（created_at 保留原值），cycle_start 按新发生时间重算；成功后失效候选缓存 |
| `delete_record(rec_id)` | `(id) -> int` | 按 id 删除，返回受影响行数；成功后失效候选缓存 |
| `delete_records(rec_ids)` | `(list) -> int` | 批量删除（记录页多选），返回受影响行数；成功后失效候选缓存 |
| `mark_resolved_batch(rec_ids)` | `(list) -> int` | 批量标记已解决（resolved=是 + resolved_at 戳），返回受影响行数 |

#### 查询与统计

| 函数 | 签名 | 说明 |
|------|------|------|
| `query_page(page_no, page_size, keyword="", cycle_start="", issue_type="", resolved="", is_initiative="", is_our_problem="")` | `-> (total, rows)` | 分页查询。周期为物化列等值过滤（走覆盖索引），其余为类型/状态/判定开关/关键词过滤 |
| `query_with_stats(...)` | `-> (total, rows, stats)` | 分页 + 同口径统计，参数同上。stats 不带 resolved 筛选（避免已解决/未解决计数退化），返回 `{total, resolved, unresolved}` |
| `query_stats_detail(keyword="", cycle_start="", issue_type="", trend_start="", trend_end="")` | `-> dict` | 售后统计弹窗详细统计（分类分布/解决率/按日趋势/按周期汇总），与四卡片/列表完全同口径 |
| `get_cycle_options()` | `() -> list` | 周期下拉选项：`SELECT DISTINCT cycle_start`（物化列，走索引）去重降序。**仅返回确有数据的周期**；触发 `_ensure_cycle_materialized` 存量兜底 |
| `get_field_candidates()` | `() -> dict` | 动态候选 `{problems, resolvers, regions, creators}`（按使用频次降序各取前 60），问题候选为空时合并预置常见项。**写后失效缓存**（insert/update/delete/导入后重建，命中 0.001ms） |
| `query_rank(level='room', limit=10, sort='total', start='', end='', cycle_start='', resolved='', is_initiative='', is_our_problem='', region='', room_name='', keyword='')` | `-> dict` | 球房/球桌售后排行（**与 Web 端 /api/stats/rank 同口径**）：WHERE 复用 _build_where + 账期物化列，单组一次 SQL 算齐 总量/未解决/我方问题/主动发起/最近发生；summary 反映同 WHERE 全量口径（不受 TOP N 截断）；球桌级联合名「球房 · 桌号」Python 侧拼装（避开双后端方言差异）；sort 白名单校验，room_name/table_no 精确下钻 |

#### 周期物化维护（S3 自愈机制）

| 函数 | 签名 | 说明 |
|------|------|------|
| `recalc_cycle_starts(batch=2000)` | `(int) -> int` | 按当前周期配置全表重算 cycle_start 物化列（分批提交，幂等），返回更新行数 |
| `recalc_cycle_starts_on_load()` | `() -> int` | 面板首载兜底：有待办标志或存量空值行才全量重算，否则仅索引探测（干净库微秒级）；重算失败标志残留、下次再试 |

#### 常用句 / 署名记忆（config/aftersale.json 持久化）

| 函数 | 签名 | 说明 |
|------|------|------|
| `load_quick_phrases()` / `add_quick_phrase(text)` | `-> list` | 常用句列表（录入页快捷输入）；新增去重置顶并写回配置 |
| `load_last_creator()` / `load_last_resolver()` / `save_last_people(creator, resolver)` | `-> str` | 上次填写的填写人/解决人记忆（非空才写，下次打开面板预填） |

#### 导出 / 导入

| 函数 | 签名 | 说明 |
|------|------|------|
| `export_xlsx(path, keyword="", cycle_start="", issue_type="", resolved="", is_initiative="", is_our_problem="")` | `-> int` | 按筛选条件导出全部记录为 xlsx（不分页），返回条数。表头与售后汇总 Excel 对齐，附加填写时间/填写人/周期列；周期列取物化值 |
| `parse_excel_rows(xlsx_path)` | `-> (headers, rows)` | 解析售后汇总 Excel（不写库），供导入预览与正式导入共用。表头按中文名定位，类型列分组首行向下填充，空行跳过，是否解决默认「否」，缺必需列抛 ValueError |
| `import_excel_rows(xlsx_path)` | `-> int` | 一次性导入历史 Excel，返回导入条数（内部调 `parse_excel_rows` 后批量写库，cycle_start 按导入时周期口径物化，成功后失效候选缓存） |

---

### database.mysql_sync

MySQL 连接工具。镜像推送（push_all/push_table/push_aftersale 及 `_DDL_*` 等）已于 2026-08-23 随机制 B 整体下线，本模块只保留连接测试，供设置页「测试连接」使用。当前双后端是「MySQL 主库 + SQLite 兜底」：`mysql_sync.enabled` 由 `backend.py` / `table_db._get_conn()` 直接路由（直连 MySQL 替代 SQLite），不经过本模块。

| 函数 | 签名 | 说明 |
|------|------|------|
| `_load_mysql_config()` | `() -> dict` | 经 `core.app_settings` 门面读 database 域的 mysql_sync 节点（password 解密；未启用返回 {}） |
| `_get_pymysql()` | `() -> module` | 取 pymysql，未安装返回 None |
| `_connect(cfg=None, use_database=True)` | `() -> conn` | 建立连接；`use_database=False` 不选库（测基础连通性） |
| `test_connection(cfg=None)` | `() -> (ok, msg)` | 先无 database 连测基础连通性，再连目标库，返回 (是否成功, 描述) |

### database.schema

表结构单一来源：8 张表的列元数据（`TABLE_COLUMNS`）与索引（`TABLE_INDEXES`）收敛于此，`to_sqlite_ddl(table)` / `to_mysql_ddl(table)` 生成双方言 DDL，消除 table_db/backend 的重复定义。

| 函数/常量 | 说明 |
|------|------|
| `TABLE_NAMES` | 8 张表名列表 |
| `ColumnDef(name, sqlite_type, mysql_type, sqlite_default, mysql_default, sqlite_extra, mysql_extra)` / `IndexDef(sqlite_name, sqlite_cols, mysql_name, mysql_cols)` / `ColumnMigration(table, col, sqlite_type, sqlite_default, mysql_type, mysql_default)` | 列 / 索引 / 列级迁移定义 dataclass（两方言类型、默认值、附加子句；字段即括号内所列） |
| `MIGRATIONS` | 列级迁移注册表（`ColumnMigration`），驱动 `table_db._migrate_sqlite_add_columns`（SQLite 自动补列）与 `_ensure_mysql_tables`（MySQL 幂等补列兜底） |
| `sqlite_alter_sql(m)` / `mysql_alter_sql(m)` | 按注册条目生成两方言 `ALTER TABLE ADD COLUMN` |
| `sqlite_alter_for(table, col)` / `mysql_alter_for(table, col)` | 按表+列名取 ALTER SQL（迁移函数内特殊补列用） |

迁移约定：SQLite 模式首次连接自动补列；MySQL 模式上线时人工执行注册表生成的 ALTER（`_ensure_mysql_tables` 仅作幂等兜底）。新增列只改本模块一处。

### database.ledger_db

跑视频记录数据层（与 `aftersale_db` 同套路，连接复用 `table_db` 双后端路由）。字段与在线模板.xlsx 对齐，系统附加 `occurred_at`（视频日期，缺省当天）/ `created_at` / `updated_at`。

| 函数 | 签名 | 说明 |
|------|------|------|
| `insert_record(record)` | `(dict) -> int` | 新增记录返回 id；`occurred_at` 缺省取当天，显式传入（看昨天的视频）则保留 |
| `update_record(record_id, record)` | `(int, dict) -> bool` | 只更新传入字段 |
| `delete_record(record_id)` | `(int) -> bool` | 按 id 删除 |
| `query_page(page_no, page_size, keyword="", category="", kind="", signer="", date_from="", date_to="", repro="")` | `-> (total, rows)` | 分页查询；日期范围按视频日期 `occurred_at` 过滤（旧数据回退 `created_at`，COALESCE+substr 取日期前缀比较）；`repro` 是/否精确过滤 |
| `get_kind_candidates(category)` | `(str) -> list` | 类别候选：模板预置 + 库中历史自由输入合并（去重保序） |
| `stats_by_signer()` | `() -> list` | 按署名汇总四分类计数（模板「计数」sheet 口径） |
| `export_xlsx(path, category="")` | `(str, str) -> int` | 按分类分 sheet 导出（表头与在线模板一致，首位附加「日期」），返回条数 |

#### 面板设置联动（记住日期 / 自动刷新，misc 域）

| 函数 | 说明 |
|------|------|
| `remember_occurred_enabled()` | 「记住上次视频日期」开关（misc 域，缺省开启） |
| `load_last_occurred()` / `save_last_occurred(occurred)` | 上次新增记录的视频日期（yyyy-MM-dd）读取/记忆（开关关闭或空值时不写） |
| `set_remember_occurred(enabled)` | 写「记住上次视频日期」开关（统一设置-面板设置-跑视频 联动入口） |
| `auto_refresh_enabled()` / `set_auto_refresh(enabled)` | 「自动刷新记录」开关（misc 域，缺省关闭；统一设置联动入口） |
| `auto_refresh_interval()` / `set_auto_refresh_interval(seconds)` | 自动刷新间隔秒数（缺省 30；仅接受 ≥5 的整数，防误配高频轮询） |
| `change_fingerprint()` | 全表轻量指纹 (条数, 最大 updated_at, 最大 id)——记录页自动刷新判据，指纹不变跳过重查 |

### database.merge_back

MySQL 从 DEGRADED 恢复 ONLINE 时把降级期间的本地增量按「时间戳 LWW」合并回 MySQL（阶段二）。与已下线的镜像推送不同：这是恢复后显式回写兜底增量。

| 函数 | 说明 |
|------|------|
| `merge_aftersale(mysql_conn)` | 售后按业务键 `(created_at, creator, table_no, problem)` LWW（有 updated_at） |
| `merge_ledger(mysql_conn)` | 跑视频按业务键 `(created_at, signer, category, kind, video_name)` LWW |
| `merge_device_mapping(mysql_conn)` | 按 device_code 主键 LWW |
| `merge_ops_tables(mysql_conn)` | 运维表（无 updated_at）退化 SQLite 优先 |
| `merge_back(progress_cb=None)` | 入口：串行执行上述合并，返回 `(ok, msg, total)` |

### database.fallback_backup

MySQL → SQLite 周备份（兜底基线刷新）：ONLINE 期间每周拉全量到本地，保证降级时本地基线 ≤7 天；降级期间不备份（避免覆盖兜底增量）。与 `merge_back` 互补。

| 函数 | 说明 |
|------|------|
| `backup_mysql_to_sqlite(progress_cb=None)` | 全量拉取替换本地表，返回 `(ok, msg, count)` |
| `maybe_backup(progress_cb=None)` | 按上次备份时间判断是否到期（≥7 天）再备份 |
| `is_backup_due()` / `get_last_backup_time()` / `set_last_backup_time(t)` | 到期判断 / 读 / 写上次备份时间（sync_meta） |

### database.data_retention

数据保留自动清理（双后端兼容，纯逻辑层不依赖 PySide6）。两类清理：

- **A 按时间过期清理**：`xqzg_status` / `kd_status` 无独立时间列，日期编码在 `file_path`（`yyyy/MM/dd` 分区，字典序即时间序），按 `file_path < today-age_days` 删除过期分区；`file_path != ''` 防御空串整表误删。每日执行幂等。
- **B 按大小清理**：范围 `aftersale_records` / `ledger_records` / `submission_log` / `health_alerts`（`device_mapping` 为设备→目录映射不纳入；`billiard_tables` / `sync_meta` 永不清理）。每 `check_interval_days` 天检查一次（`sync_meta.last_size_check` 记录），表大小超过 `max_size_gb` 时按日期桶从最早逐日删除直到低于 `min_size_gb`；最近 `min_keep_days` 天保护期内不删。
- 表大小统计：MySQL 用 `information_schema.tables`（`data_length + index_length`）；SQLite 用 `dbstat` 虚表（不可用时跳过该表）。
- 日期桶提取：按 `occurred_at`（空值回退 `created_at`）取前 10 位日期前缀（与跑视频面板筛选同口径）；`submission_log` 用 `created_at`；`health_alerts` 用 `updated_at`。

配置读取 `config/database.json` 的 `data_retention` 节点（经 `backend._read_mysql_settings` 同款免 core 直读，规避 PySide6 依赖链；缺省回落模块内 `DEFAULT_CONFIG`；`tables` 白名单过滤未知表名）。

| 函数 | 签名 | 说明 |
|------|------|------|
| `run_cleanup(progress_cb=None)` | `(callable?) -> (bool, str, int)` | 执行一轮清理（A + B），返回 (ok, msg, 删除总行数) |
| `is_enabled()` | `() -> bool` | 总开关（worker 挂载时判断） |

### database.sqlite_io

SQLite 只读工具（数据层基础设施）：只读、列交集、缺列补 None、表不存在返回空列表。原镜像推送 `_read_sqlite` 的列交集语义由本模块承接。

| 函数 | 说明 |
|------|------|
| `read_sqlite_table(conn, table, columns)` | 按实际列 ∩ 请求列读取，返回 dict 列表（缺列补 None） |

### database.mysql_sync_card_logic

MysqlSyncCard 入口判定纯函数（无 PySide6 依赖，可单测）：根据表单配置决定「测试连接」/同步相关操作是否可执行及提示文案。未启用 MySQL 时统一提示「当前数据库为本地 SQLite，未启用 MySQL」。

| 函数 | 说明 |
|------|------|
| `should_attempt_sync(cfg)` | 是否可执行同步操作（返回 (ok, hint)） |
| `should_attempt_test(cfg)` | 是否可执行连接测试 |

---

## windows/ 独立窗口层

### windows.remote_session.sftp_window（SFTPWindow 双面板文件管理）

#### 类 `SFTPWindow`

SFTP 双面板文件管理窗口（QDialog）。

```python
SFTPWindow(host, port, username, password,
           server_name='', log_callback=None, parent=None)
```

**功能**：
- 本地/远程双面板文件浏览（TreeWidget）
- 上传/下载（单文件 + 整目录递归）
- 删除/重命名/创建目录/创建文件
- 传输队列（暂停/恢复/取消）
- 实时搜索过滤（Ctrl+F）
- 右键上下文菜单（RoundMenu）

**列定义**：
- 本地：文件名(220px) | 大小(80px) | 类型(60px) | 修改时间(100px)
- 远程：文件名(220px) | 大小(80px) | 类型(60px) | 权限(80px) | 修改时间(130px)

#### 类 `SFTPPanel`

SFTP 文件管理面板形态（QWidget，可嵌入标签页容器，也可独立使用）；`SFTPWindow` 为其独立对话框包装。

---

### windows.remote_session.ssh_terminal（SSHTerminalWindow 交互式终端）

#### 类 `SSHTerminalWindow`

SSH 交互式终端窗口（invoke_shell PTY + ANSI 渲染）。

```python
SSHTerminalWindow(host, port, username, password,
                  log_callback=None, parent=None)
```

**功能**：
- 直接键盘输入（Windows Terminal 风格）
- Tab 命令/路径补全
- 上下键命令历史
- Ctrl+C/D/L 控制键
- ANSI 彩色输出渲染
- 全屏应用支持（nano/vim 备用屏幕切换）
- 外部客户端打开（CMD / Xshell）

**安全关闭策略**：
- channel 设置 0.1s recv 超时
- closeEvent 仅设 stop 标志并等待 reader 线程退出
- reader 线程退出后再关闭 transport

**其他**：`SSHTerminalPanel(QWidget)` 可嵌入面板形态；`SshCommandEditDialog(QDialog)` 常用命令管理（增删命令写入 `ssh_commands` 配置键）；模块级 `get_session_log_dir()` 返回 SSH 会话日志目录（logs/ssh_sessions，与 conn_logger 同级机制）。

---

### windows.remote_session.rdp_window（RDPWindow 远程桌面嵌入）

#### 类 `RDPWindow`

远程桌面嵌入窗口（mstsc.exe 窗口嵌入）。

```python
RDPWindow(host, port, username, password,
          server_name='', log_callback=None, parent=None)
```

**实现架构（持续看门狗）**：
1. `cmdkey` 静默注册 RDP 凭据
2. 启动 `mstsc.exe /v:host:port`
3. 每 800ms 看门狗轮询：查找窗口 → 嵌入
4. `AttachThreadInput` + `SetParent` 跨进程嵌入
5. 去除标题栏、同步尺寸、进程退出检测

**窗口查找策略（评分制）**：
- 通道1：按启动进程 PID
- 通道2：按所有 mstsc.exe 进程 PID（Win11 进程委托）
- 通道3：全局类名兜底

#### 类 `RDPPanel`

远程桌面面板形态（QWidget，嵌入系统 mstsc.exe 窗口到应用内，可嵌入标签页容器）；`RDPWindow` 为其独立对话框包装。

---

### windows.single_video_dialog（SingleVideoDialog 单杆视频参数对话框）

#### 类 `SingleVideoDialog`

单杆视频参数对话框。继承 `MessageBoxBase`（透明模态窗口）。工具页 `SingleVideoWork` 直接从本模块导入默认值常量（`_DEFAULT_SESSION_CODE/_DEFAULT_FORMAT/_DEFAULT_USER_x/_DEFAULT_AVATAR_x` 等）内嵌表单；独立对话框形态保留为回退兜底（`UiMixin._on_open_single_video` 旧入口）。

```python
SingleVideoDialog(parent, settings=None)
```

**场次自动识别**：选择日志文件后，从文件名解析 `session_date`（正则 `^(\d{8})`，如 `20260810_230635.log` → `20260810`），并自动生成 `session_code`（`{日期}_{21位随机串}`，字符集 `[A-Z0-9]`，与球桌接口 code 同格式）。

**文件对话框异步化**：使用 `QFileDialog.open()` 非阻塞模式（而非 `getOpenFileName` 阻塞调用），选择期间对话框锁定（`_phase = "browsing"` 禁用按钮/拦截 closeEvent），结果经 `fileSelected`/`finished` 信号异步回调——避免从透明模态窗口弹原生对话框导致的卡死。

**关键方法**：

| 方法 | 说明 |
|------|------|
| `collect_params()` | 校验并收集生成参数（日志路径、session_date/code、帧区间、选手信息等） |
| `append_line(text)` | 追加输出日志行 |
| `enter_running()` / `enter_done(path)` / `enter_failed()` | 运行中/成功/失败状态切换 |
| `closeEvent(e)` | 运行中（running/browsing）拦截关闭 |

表单控件使用 `CompactSpinBox`（紧凑版微调框，继承 QSpinBox，API 兼容）。

---

### windows.remote_session.remote_session_window（RemoteSessionWindow 远程会话）

#### 类 `RemoteSessionWindow`

远程会话窗口（`FramelessWindow`），展示 frpc 日志与隧道状态，由 `RemoteSessionManager` 统一管理。

---

### windows.remote_session.window（TunnelPanelWindow 当前隧道面板）

#### 类 `TunnelPanelWindow`

「当前隧道」面板（`FramelessWindow`），主窗口远程面板入口打开，展示全局活跃隧道；内置 SFTP 传输中断二次确认、frpc 状态联动与 `_apply_smooth_mode` 表格平滑滚动适配。

---

### windows.remote_session.conn_diag_panel（ConnDiagPanel 连接诊断）

#### 类 `ConnDiagPanel` / `ConnDiagWidget`

连接诊断面板（`QDialog`，设置-工具行入口独立弹窗）；`ConnDiagWidget(QWidget)` 为诊断主体（2026-09-07 自 QDialog 抽出，可嵌入 RemoteHub 等容器）。

#### 模块级函数（连接日志解析与聚合）

| 函数 | 说明 |
|------|------|
| `parse_log_text(text, source)` | 解析单个日志文件文本 → 记录列表（保持文件内时序） |
| `load_all_records(log_dir)` | 按从旧到新顺序读取归档（.3→.2→.1）+ 当前日志，合并为全局时序列表 |
| `is_success_record(r)` / `is_conn_fail_record(r)` | 单条记录成功/失败判定 |
| `aggregate_stats(records)` | 对记录列表聚合连接质量统计（纯函数，便于脚本验证） |

---

### windows.management.moyu_widgets（MoyuReaderWidget 摸鱼阅读器）

#### 类 `MoyuReaderWidget`

摸鱼阅读器（`QWidget`），内置文本阅读（TXT/粘贴）、网页正文抓取；与 2048/贪吃蛇/扫雷 小游戏同属摸鱼中心（`GamePage`）。

#### 类 `Game2048Widget(QWidget)` / `SnakeWidget(QWidget)` / `MinesweeperWidget(QWidget)`

小游戏控件（信息栏 + 棋盘）：`Game2048Widget`（得分/最高分/重开）、`SnakeWidget`（得分/最高分/难度/控制按钮）、`MinesweeperWidget`（雷数/用时/最快纪录/难度/控制）；成绩按难度分档存 `moyu_state.json`。

| 模块级函数 | 说明 |
|------|------|
| `load_moyu_state()` | 读摸鱼状态，文件缺失/损坏返回空字典 |
| `save_moyu_state(patch)` | 读-改-写合并落盘，失败静默（摸鱼状态丢失不影响主业务） |

---

### windows.management.window（ManagementPanelWindow 运维管理面板）

#### 类 `ManagementPanelWindow`

运维管理面板宿主（qfluentwidgets `FluentWindow` + 左侧导航），六个功能页面。由主窗口「球桌管理」按钮打开（`UiMixin._on_open_table_panel`），支持 `python -m windows.management_panel` 独立调试；各页面类实现于 `windows/management/` 子包（table_page / device_page / health_page / admin_settings / widget_page / game_page / trend_page）。

```python
ManagementPanelWindow(parent=None)
```

**页面构成**：

| 页面 | 类 | 说明 |
|------|------|------|
| 球桌管理 | `TablePage` | wechat2-billiard 球桌数据：表格/搜索/分页/列筛选/右键复制/手动添加记录；含 `code`（设备编码）列，默认隐藏可在「筛选」菜单勾选显示（`_hidden_cols = {在线状态, 设备编码}`） |
| 设备状态 | `DevicePage` | kd / xqzg 数据源切换（`get_active_api_source`），按日期分区查看；集成图片迁移 |
| 设备健康度管理 | `HealthPage` | 健康度异常告警：每 30 分钟全量拉取 health（`TableFetchWorker` → `sync_health_alerts` 落库），每 1 小时重载展示；阈值 4000/5000/40 万；支持标记已处理 |
| 管理设置 | `AdminSettingsPage` | 数据源选择（kd/xqzg）、双接口账号密码、测试连接，合并写入 `settings.json` |
| 控件测试 | `WidgetPage` | FluentIcon 图标库（175 个，搜索过滤、点击复制枚举名）+ qfluentwidgets 控件墙（按钮/输入/日期/弹窗等分组演示，可直接交互） |
| 小游戏 | `GamePage` | 摸鱼中心（小说阅读 / 2048 / 贪吃蛇 / 扫雷）；后两者带难度选择，成绩按难度分档存 `moyu_state.json` |
| （隐藏）健康趋势 | `TrendPage` | 健康度趋势看板（C3）：突增预警 + 单设备趋势折线 + TOP N 排行，仅 kd 数据源可用；导航入口已注释隐藏，恢复取消注释即可 |

**图片迁移交互（DevicePage，实现于 `windows/management/device_page.py`）**：

- `总数`(pic_total) / `正常`(normal_count) / `操作`(except_count) 三列为链接色可点击单元格（`_FILE_VIEW_FIELDS`）
- 点击后右侧滑出 `FileListPanel`（QPropertyAnimation，宽 360，需 `WA_StyledBackground` 才不透明）展示 [分类, 文件名]
- 点击文件条目弹 RoundMenu 四选项（问题/精度/使用/废弃，`MIGRATE_DEST_OPTIONS`）→ `DevicePage.migrate_file` → `MigrateImageWorker` 迁移单文件 → 成功后 `_silent_refresh` 静默重拉刷新

| DevicePage 关键方法 | 说明 |
|------|------|
| _on_cell_clicked(row, col) | 单元格点击 → 打开文件面板 |
| migrate_file(fname, src_cat, dest_cat) | 发起单文件迁移 |
| _silent_refresh() | 迁移后静默重拉当前数据源 |

**模块级辅助**（`windows/management_panel.py` shim 提供）：_load_settings / _save_settings（settings.json 合并读写）、_copy_table_selection（表格选中内容复制）、FILE_FIELD_CATEGORIES（文件字段 → 中文分类）。

---

### windows.aftersale.window（AftersalePanelWindow 售后面板）

#### 类 `AftersalePanelWindow`

售后面板宿主（qfluentwidgets `FluentWindow` + 左侧导航，风格仿运维管理面板）。由主窗口 `_on_open_aftersale`（单例复用，**内置窗口**，不拉起外部进程）或运维面板入口打开，支持 `python -m windows.aftersale_panel` 独立调试。数据层 `database/aftersale_db.py`，所有 DB 读写经 `AftersaleDBWorker` 后台线程，UI 零阻塞。窗口类本身负责导航装配与联动：`_on_cycle_saved`（周期设置保存 → 记录页刷新）、`open_records_for_table(table_no)`（球桌右键跳转记录页预筛选）、`_on_rank_jump_records`（Web 排行页联动入口）、`_on_nav_changed`（页面切换联动）。

**独立打包（单文件）**：`AfterSale.spec` 用 PyInstaller onefile 模式打包为 `dist/aftersale.exe`，内置全部依赖、独立分发。单文件模式下 `sys._MEIPASS` 为临时解压目录，入口文件顶部会把 `table_db` 的 DB 路径重定向到 exe 旁 `database/tables.db`（首启从 `_MEIPASS` 复制种子库），保证数据持久化；与完整版 AutoWork 的数据相互独立、互不关联。

```python
AftersalePanelWindow(parent=None)
```

**页面构成**：

| 页面 | 类 | 说明 |
|------|------|------|
| 填写录入 | `EntryPage` | 售后问题登记表单（`AftersaleForm`），提交后写库并通知记录页刷新 |
| 记录与统计 | `RecordsPage` | 筛选（周期/类型/状态/是否我们发起/是否我方问题/关键词）+ 分页 + 统计 + 编辑/删除 + 批量操作（勾选标记已解决/批量删除）+ 导出 xlsx/导入 Excel |
| 设置 | `SettingsPage` | 统计周期设置（`CycleSettingsPage`）+ 数据库设置（`MysqlSyncCard` sync_scope="aftersale"） |

**共享表单 `AftersaleForm`**（录入页与编辑弹窗复用，实现于 `windows/aftersale/form.py`）：字段与售后汇总 Excel 对齐 + 系统附加字段。球房输入防抖搜索球桌库，候选点选/唯一命中自动带出桌号/SNK/城市；发生时间步进按钮补录历史日期；「是否我们发起售后」（`is_initiative`，默认否）与「是否我方问题」（`is_our_problem`，默认是）两个判定用 `YesNoSegment` 分段开关，参与筛选与统计口径。

| AftersaleForm 方法 | 说明 |
|------|------|
| load_candidates(cands) | 填充动态候选（问题/解决人/地区），保留已输入文本 |
| set_values(rec) / collect() / validate() / clear_form() | 编辑回填 / 收集值 / 必填校验 / 清空 |

**周期筛选**（`RecordsPage`，实现于 `windows/aftersale/records.py`）：周期下拉选项来自 aftersale_db 的 `get_cycle_options`（库中记录实际归属周期）；当前周期仅在库中有数据时出现。切换周期后列表与统计按同一套归属规则重查，一一对应。

| RecordsPage 关键方法 | 说明 |
|------|------|
| _load_cycles_then_data() | 先异步拉周期选项填充下拉，再加载数据 |
| _on_cycles_loaded(cycle_starts) | 填充周期下拉（当前周期仅有数据时显示），默认选中当前周期否则全部周期 |
| _load() | 按当前筛选异步查询（分页 + 统计一次返回） |
| set_keyword(kw) | 球桌管理右键跳转：按桌号预筛选，周期放宽为全部 |

**弹窗**：`EditRecordDialog`（MessageBoxBase）编辑记录（复用共享表单）；`ImportPreviewDialog`（QDialog）导入预览（字段要求提示 + 前 20 行解析效果 + 确认导入）。下拉组件统一 qfluentwidgets：`ComboBox`（周期/非可编辑筛选）、`EditableComboBox`（类型等可编辑下拉）、`YesNoSegment`（是/否判定）、`ZhDatePicker`（日期）。

**周期设置 `CycleSettingsPage`**：统计周期模式单选（周二起默认/自然周/自定义起始日+天数），保存写 settings.json 并 `saved` 信号通知记录页刷新周期下拉与统计。

---

### windows.run_video.window（LedgerPanelWindow 跑视频面板）

#### 类 `LedgerPanelWindow`

跑视频面板宿主（qfluentwidgets `FluentWindow` + 左侧导航），字段与在线模板.xlsx 数据 sheet 对齐。由主窗口 `_on_open_ledger`（单例复用，预填当前球桌会话）打开，支持 `python -m windows.ledger_panel` 独立调试。数据层 `database/ledger_db.py`，DB 读写经 `AftersaleDBWorker` 后台线程。窗口类公开方法：`open_entry_with_context(ctx)`（主界面「跑视频」入口：切到填写录入页并预填会话上下文）。

```python
LedgerPanelWindow(parent=None)
```

**页面构成**：

| 页面 | 类 | 说明 |
|------|------|------|
| 填写录入 | `EntryPage` | 共享表单 `LedgerForm`，提交后写库并通知记录页刷新 |
| 记录与统计 | `RecordsPage` | 指标卡 + 日期/分类/类别/署名/复现筛选 + 分页 + 编辑/删除 + 署名统计 + 按分类分 sheet 导出 xlsx |
| 设置 | `SettingsPage` | 默认署名 + 数据库设置（`MysqlSyncCard` sync_scope="ledger"） |

**共享表单 `LedgerForm`**（实现于 `windows/run_video/form.py`）：分类（问题/未复现/精度/使用）→ 类别 → 球房 → 视频名 → 帧数 → 日期 → 描述/备注 → 复现 → 新程序 → 署名。分类必填，切换联动类别候选（模板预置 + 库中历史自由输入）；类别为可编辑下拉（`EditableComboBox`，支持手输新类别）；日期为视频日期（`ZhDatePicker`，默认当天可翻历史日期）；复现/新程序用 `YesNoSegment` 是/否开关（默认「否」）；描述与备注并排（70/30）。

| LedgerForm 方法 | 说明 |
|------|------|
| prefill(ctx) | 按主界面会话上下文预填球房/视频名/帧数/署名/分类（只填空值） |
| set_values(rec) / collect() / validate(show_errors=True) / clear_form() | 编辑回填 / 收集值 / 必填校验（show_errors=False 时静默校验不显示红框，供必填进度用）/ 清空 |
| _on_category_changed(category) | 分类切换：类别候选异步联动 + 重建补全器 |

**记录与统计**（实现于 `windows/run_video/records.py`）：指标卡（总记录/问题/未复现/精度/使用）与列表同口径，随日期范围联动。日期筛选模式：全部日期/今天/近7天/近30天/本月/自定义（默认今天，基准日期可翻历史，结束日历仅自定义显示）；`_date_range` 按档计算区间（近7天=基准前推 6 天、近30天=前推 29 天、本月=自然月、自定义起止倒置自动交换），按视频日期 `occurred_at` 过滤（旧数据回退 `created_at`）。首次显示自动加载（showEvent + `_loaded_once`）；自动刷新定时器按指纹比对（`ledger_db.change_fingerprint`）跳过无变化重查。

**弹窗**：`EditLedgerDialog`（MessageBoxBase）新增/编辑记录（复用共享表单，`continuous=True` 连续录入模式：保存并继续不关窗）；`SignerStatsDialog`（MessageBoxBase）署名统计（问题/未复现/精度/使用/总和）。

---

### windows.mysql_sync_card（MysqlSyncCard MySQL 连接配置卡片）

#### 类 `MysqlSyncCard`

可复用 MySQL 连接配置卡片（运维面板 / 售后面板 / 跑视频面板共用）。连接表单 + 启用开关 + 测试连接/保存按钮。自动同步与「立即同步」已随镜像推送机制 B 下线，启用开关只控制直连路由；配置读写 DPAPI 加密落盘，以磁盘最新内容为 base 合并写（防双缓存覆盖）。

```python
MysqlSyncCard(parent=None, sync_scope="ops")
```

- `sync_scope="ops"`：运维业务数据，卡片标题「服务器SQL 同步」
- `sync_scope="aftersale"`：售后记录，卡片标题「数据库设置」
- `sync_scope="ledger"`：跑视频记录，卡片标题「数据库设置」（仅影响说明文案，开关与连接配置共用）

`enabled` 开启后 MySQL 完全替代本地 SQLite（`backend.py` / `table_db._get_conn()` 直连路由），关闭回到本地 SQLite；MySQL 不可用时自动降级本地，恢复后 `merge_back` 自动合并兜底增量。

| 方法 | 说明 |
|------|------|
| `load()` | 从 settings.json 加载当前配置填充表单 |
| `_on_test()` / `_on_save()` | 异步测试连接 / 合并写配置即时生效（启用时 backend 各线程下次取连接即走 MySQL） |

---

### windows.remote_session.forensic_report（SSH 故障取证包）

SSH 连接失败时一键生成诊断取证包（模块级函数 + `ForensicWorker` 后台线程）。

| 函数 | 签名 | 说明 |
|------|------|------|
| `get_forensic_dir()` | `() -> str` | 取证报告目录 `{app_dir}/logs/forensic`（自动创建 + 闭环清理：保留 90 天且不超 200 个） |
| `lookup_table_info(snk, host)` | `(str, str) -> dict` | 从 billiard_tables 反查球桌信息（匹配优先级：snk_code 精确 → remark 含 snk → remark 含 host） |
| `lookup_kd_status(table_id, snk)` | `(str, str) -> dict` | 查 kd_status 最新分区关键字段（优先 table_id TRIM 匹配，再按 snk 当 device_code） |
| `read_session_tail(path, n=SESSION_TAIL_LINES)` | `(str, int) -> str` | 读会话日志最近 n 行 |
| `collect_conn_log(host, limit=CONN_LOG_ENTRIES)` | `(str, int) -> str` | 从连接日志及归档中提取该 host 最近记录（时间正序） |
| `build_ai_evidence(cmd_results)` | `(list) -> str` | 拼装送 AI 的取证证据（仅成功命令输出，单条/总量截断） |
| `analyze_with_ai(evidence)` | `(str) -> str` | 调用所选 AI 厂商 OpenAI 兼容接口分析证据，返回 Markdown 文本 |
| `build_forensic_report(meta, cmd_results, table_info, kd_info, session_tail, conn_log, ai_analysis="", ai_error="", ai_label="")` | `(...) -> str` | 组装完整 Markdown 报告 |

#### 类 `ForensicWorker`

后台逐条执行诊断命令组并生成报告（exec_command 独立 channel，不阻塞交互）。

**信号**：`line` Signal(str)——逐条命令执行进度；`done` Signal(str)——报告文件路径；`error` Signal(str)——失败信息。

---

### windows.management.image_viewer（ImageViewerDialog 图片查看）

#### 类 `ImageViewerDialog`

设备状态图片查看卡片对话框（左右键翻页，支持分类迁移）。

```python
ImageViewerDialog(entries, index, file_path, device_code, device_page,
                  can_migrate=True, dest_options=(), btn_qss=None, parent=None)
```

- `_ImageFetchWorker`：后台下载图片字节流（静态资源无需认证头，可 cancel）
- `_image_urls(fname, src_cat)`：候选 URL 展开——分类目录优先、`pic/` 目录兜底，文件名按变体展开（原名优先，去 Django 去重后缀的原图兜底）
- 模块级 `is_image_file(fname)`：按扩展名判断是否图片文件

---

### windows.tools.port_fake（PortFakeWidget 虚假端口占用）

#### 类 `PortFakeWidget`

虚假端口占用工具（工具菜单「端口占用」）——真实 `bind + listen` 模拟服务占用，`netstat -ano` 可见 `LISTENING`。

```python
PortFakeWidget(parent=None)
```

- `_occupy()`：占用输入框端口（保持监听）；支持多端口同时占用
- `_release_all()` / `_release_one(port)`：释放端口
- `closeEvent`：页面/窗口关闭时自动释放全部端口，避免占用残留
- 随机端口范围 20000~60000（`_RANDOM_MIN` / `_RANDOM_MAX`）

---

### windows.stat_charts

统计图表自助分析窗口（pygwalker Graphic Walker）：售后/跑视频记录页「统计图表」按钮触发，独立窗口内拖拽式自助分析与预置图表。

#### 类 `StatsOpener`

无 UI 宿主的打开器（QObject），记录页持有实例，信号驱动窗口生命周期。

| 方法 | 说明 |
|------|------|
| `open_analysis(filters=None)` | 后台按当前筛选聚合 DataFrame → pygwalker HTML 渲染到独立窗口。构造时注入 `builder`（记录页的取数回调，复用 `aftersale_db` / `ledger_db` 同口径查询）与 `kind`（售后/跑视频） |

**信号**：`finished` Signal(bool, str)——(是否成功, 描述/输出路径)。

> pygwalker 首次 import 在主线程执行（约 1-2s，已知权衡，后续打开走 import 缓存）；数据导出走 DataFrame（记录量大时取数受分页查询同口径约束）。

---

### windows.aftersale_panel

售后面板 **re-export shim**：原 2275 行单体文件已按页面/职责拆分为 `windows/aftersale/` 包（window / entry / records / form / dialogs / settings 等），本模块仅做向后兼容的 re-export（`AftersalePanelWindow` 实现于 windows/aftersale/window.py），`python -m windows.aftersale_panel` 独立调试入口保留。

### windows.ledger_panel

跑视频面板 **re-export shim**：功能实现于 `windows/run_video/` 包（window / entry / records / form / edit_dialog 等），本模块仅 re-export（`LedgerPanelWindow` 实现于 windows/run_video/window.py）。

### windows.management_panel

运维管理面板 **re-export shim**：原 4483 行单体文件已按页面/职责拆分为 `windows/management/` 包（window / table_page / device_page / health_page / admin_settings / widget_page / game_page / device_common / image_viewer / moyu_widgets 等），本模块仅 re-export（`ManagementPanelWindow` 实现于 windows/management/window.py）。

### windows.update_dialog

#### 类 `UpdateDialog`

更新对话框：新版本详情 → 下载进度 → 安装重启（`MessageBoxBase` 状态机，与 SingleVideoDialog 同模式：对话框持有 Worker，yesButton 按 phase 分发动作）。phase 状态：found（发现新版，确认下载）→ downloading（下载中，进度条 + 可取消）→ ready（下载完成，确认安装重启）→ error（失败，可返回 found 重试）。

| 方法 | 说明 |
|------|------|
| `reject()` | 取消当前动作（下载中先停 Worker 再关窗，需用户确认） |
| `closeEvent(e)` | 下载中拦截关闭（防误触丢包） |

---

## main_window/ 主窗口层

### main_window.main_window

#### 类 `MainWindow`

主窗口类，组合所有 Mixin（进程管理 / 设置 / 远程 / UI / 自动更新）：

```python
class MainWindow(SettingsMixin, ProcessMixin, RemoteMixin, UIMixin, UpdateMixin, FluentWindow)
```

内部还有 `_DbSettingsDialog`（MessageBoxBase，内嵌 `MysqlSyncCard` 的数据库设置弹窗）、`_LogLoadWorker`（后台读日志 + 高亮匹配）、`_KdStatusQueryWorker`（后台反查 kd 记录分类）三个私有类。

#### 核心公开方法

| 方法 | 说明 |
|------|------|
| `switch_to_page(page)` | 统一页面路由：Hub 子页 → 切一级 Hub 再容器内切换；其余走 stackedWidget |
| `open_aftersale_records_for(table_no)` | 球桌右键「查看售后记录」→ 售后 Hub 记录页按桌号预筛选 |
| `open_hub_popout(hub)` | Hub 右上「弹出面板」入口：把该面板复刻为独立窗口打开 |
| `table_page()` / `device_page()` / `health_page()` / `records_page()` | Hub 子页访问器（ManagementHub / AftersaleHub 供跨页联动刷新） |
| `on_flush_clicked()` | 刷新设备列表和程序列表 |
| `on_open_daily_clicked()` | 打开 CPP 日志文件 |
| `on_open_dir_clicked()` | 打开当前设备目录 |
| `focus_log_file(device_dir, date_str, log_fname)` | C6 反向跳转入口：切换到指定设备+日期并选中日志文件 |
| `on_id_selected(item)` / `on_video_selected(item)` | ID 列表 / 日志目录列表选中事件（加载对应目录/日志内容） |
| `on_log_selected(item)` / `on_log_double_clicked(item)` | 日志选中 / 双击（解析日志、更新 cfg.json，双击额外启动程序） |
| `_apply_startup_default_page()` | 启动时按配置 `startup_default_page` 切换默认展示界面（`_build_hub_pages` 末尾 singleShot(0) 调用；非法值回退工作台，2026-09-19） |
| `_show_info_bar(message, message_type="info", title=None, duration=2500)` | 统一 InfoBar 提示（兼容入口，内部转调 `core.utils.show_info_bar`，位置 BOTTOM_RIGHT、标题自动映射） |
| `closeEvent(event)` | 主窗口关闭时统一释放所有子进程和远程会话资源，防止孤儿进程 |

> 进程管理方法（启动/结束/三端）见 [ProcessMixin](#main_windowprocess_mixin)，设置方法见 [SettingsMixin](#main_windowsettings_mixin)，UI/主题方法见 [UiMixin](#main_windowui_mixin)，远程方法见 RemoteMixin，更新方法见 [UpdateMixin](#main_windowupdate_mixin)——各自实现于对应 Mixin 模块。

---

### main_window.settings_mixin

#### 类 `SettingsMixin`

配置管理（配置门面读写、路径加载、快捷键）。

| 方法 | 说明 |
|------|------|
| `_load_settings()` | 返回内存缓存的配置 dict |
| `_save_settings(data: dict)` | 合并写入 settings.json |
| `_reload_settings_cache()` | 从磁盘重新加载到缓存 |
| `_load_paths()` | 加载路径配置到实例属性 |
| `_restore_exe_selection()` | 恢复上次选择的程序 |
| `_init_shortcuts()` | 绑定全局快捷键 |
| `_get_shortcut_settings()` | 获取快捷键配置 dict |

**默认快捷键**：

| 功能 | 默认键 | 配置键 |
|------|--------|--------|
| 刷新 | F5 | `shortcut_flush` |
| 播放/结束 | Space | `shortcut_start` |
| 打开目录 | Ctrl+O | `shortcut_open_dir` |
| 暂停/恢复 | P | `shortcut_pause` |
| 聚焦帧数框 | Ctrl+G | `shortcut_focus_frame` |
| 启动三端 | Ctrl+T | `shortcut_start_three` |
| 查看CPP日志 | Ctrl+L | `shortcut_open_daily` |
| 打开配置 | Ctrl+, | `shortcut_open_config` |
| P2P面板 | F9 | `shortcut_p2p_panel` |
| 跑视频页 | Ctrl+1 | `shortcut_ledger_panel` | 跳转「跑视频」页并预填当前球桌会话 |
| 售后页 | Ctrl+2 | `shortcut_aftersale_panel` | 跳转「售后」页 |
| 运维管理页 | Ctrl+3 | `shortcut_table_panel` | 跳转「运维管理」页 |

---

### main_window.process_mixin

#### 类 `ProcessMixin`

进程管理（QProcess、三端启动、分辨率切换、暂停/恢复）。

| 方法 | 说明 |
|------|------|
| `on_start_clicked()` | 启动识别程序（含 detect.json 解码前置） |
| `on_end_clicked()` | 强制终止运行中程序 |
| `on_start_three_clicked()` | 切换三端启动/关闭 |
| `_start_three_programs()` | 依次启动识别端/后端/前端（间隔3秒） |
| `_stop_three_programs()` | 关闭三端并恢复分辨率 |
| `_capture_current_resolution()` | 捕获当前显示模式 |
| `_restore_resolution(mode)` | 恢复显示模式 |
| `_on_pause_clicked()` | 挂起/恢复进程（NtSuspendProcess） |

---

### windows.remote_session.remote_mixin

#### 类 `RemoteMixin`

远程连接管理（P2P 面板、XTCP/TCP 双模式、frpc 管理、窗口启动；实现于 `windows/remote_session/remote_mixin.py`）。

| 方法 | 说明 |
|------|------|
| `_init_p2p_panel()` | 初始化远程面板 |
| `_on_p2p_toggled(checked)` | 切换远程面板显隐 |
| `_on_p2p_add()` | 添加 visitor / TCP 服务器 |
| `_on_p2p_delete()` | 删除 visitor / TCP 服务器 |
| `_on_p2p_connect()` | 建立连接（XTCP: 启动 frpc → TCP: 直连） |
| `_on_p2p_disconnect()` | 断开连接（停止 frpc） |
| `_on_sftp_btn_clicked()` | 打开 SFTP 文件管理窗口 |
| `_on_ssh_terminal_btn_clicked()` | 打开 SSH 终端窗口 |
| `_on_rdp_btn_clicked()` | 打开远程桌面窗口 |

#### 类 `TablePickerMenu` / `TablePickerComboBox`

球桌库选择组件（P2P 访客球桌搜索联动用）：`TablePickerComboBox(EditableComboBox)` 输入防抖搜球桌库 + 候选带出 serverName；`TablePickerMenu(CompleterMenu)` 球桌候选弹层（已展示时原位刷新，不重跑淡入动画）。

---

### main_window.ui_mixin

#### 类 `UIMixin`

UI 辅助（状态栏、菜单栏、右键菜单、主题切换、布局切换）。模块内另有 `AboutDialog`（关于弹窗）、`VisibleAcrylicMenu`（增强可见度亚克力菜单）、`_ShortcutKeyEdit`（Fluent 风格快捷键录入框）、`NewLogDialog`（视频/日志批量整理对话框）等辅助类。

| 方法 | 说明 |
|------|------|
| `_init_statusbar()` | 初始化底部状态栏 |
| `_init_context_menus()` | 初始化右键菜单 |
| `_init_menubar()` | 初始化菜单栏（含「工具」菜单：单杆视频 / 端口占用 / 视频/日志批量整理 / 上传清单——各入口统一经 `_on_open_tool_hub` 跳转工具页对应工作区） |
| `_apply_theme()` | 应用深色/浅色主题 |
| `_parse_theme_color(settings)` | [静态] 解析主题强调色：优先 `theme_color`（HEX），兼容旧 `highlight_color`（RGB 列表） |
| `_apply_theme_color()` | 从配置加载主题强调色到内存（`_apply_theme` 时应用） |
| `_on_theme_color()` | 弹出主题强调色选择对话框（功能菜单「主题颜色设置」），选色后经 `_apply_theme_color_set` 生效 |
| `_on_theme_color_reset()` | 还原默认主题强调色（功能菜单「还原默认主题色」），已是默认色时提示不重复执行 |
| `_apply_theme_color_set(color)` | 应用主题强调色：持久化 `theme_color` + `setThemeColor` 全局即时生效 + 日志/InfoBar |
| `_apply_font_size()` / `_apply_font_family()` | 应用字号 / 字体设置 |
| `apply_dpi_scale(settings)` | [静态] 在 QApplication 创建后应用 DPI 缩放（settings 为合并配置 dict） |
| `_effective_is_dark(settings)` | [静态] 判断是否深色主题 |
| `_apply_layout()` | 应用布局模式（经典/默认） |
| `_on_open_single_video()` | 工具菜单「单杆视频」回退兜底（正常路径为 `_on_open_tool_hub("single_video_work")` 跳工具页）：校验 Worker 空闲 → 延迟导入探测 cv2/numpy → 打开 `SingleVideoDialog` 并注入 `_start` 回调 |
| `_on_open_tool_hub(work=None)` | 工具类入口统一跳转二期独立工具页（`main_window.tool_hub`）；`work` 为工作区 objectName（single_video_work / port_fake_work / upload_list_work / newlog_work） |
| `_on_open_port_fake()` | 工具菜单「端口占用」：弹窗真实监听指定端口模拟服务占用（`PortFakeWidget`） |
| `_on_newlog_organize()` | 工具菜单「视频/日志批量整理」：按 Excel 署名筛选批量归类（`NewLogDialog` + `NewLogWorker`），支持一键打包上传 |

#### 类 `AboutDialog` / `NewLogDialog` / `VisibleAcrylicMenu`

- `AboutDialog(MessageBoxBase)`：关于弹窗——应用名/版本号 + GitHub 链接 + 开源依赖库清单
- `NewLogDialog(MessageBoxBase)`：视频/日志批量整理对话框——`append_line` 输出、enter_running/enter_organized/enter_uploading/enter_failed 状态机、上传字节进度 `set_upload_percent`、运行中拦截 closeEvent
- `VisibleAcrylicMenu(AcrylicMenu)`：增强可见度亚克力菜单（深色/浅色主题均有明显磨砂玻璃效果；`_VisibleAcrylicView` 亚克力关时改绘纯色背景）
- `_ShortcutKeyEdit(LineEdit)`：Fluent 风格快捷键录入框——点击聚焦后按下目标组合键即记录，`keySequence()` 取当前序列

---

### main_window.hub_pages（SettingsHubPage 统一设置页，原 SettingsDialog 已下线）

统一设置页（Watt Toolkit 式：左标题 + 右 SegmentedWidget 分页），收编原菜单栏全部设置 Action 与三个面板的设置项。**原 `main_window/settings_dialog.py` 已删除**（2026-09-06），日志规则迁 `core/log_rules.py`，控件组件在 `main_window/setting_cards.py`（SettingGroup 组标题 + SettingRow 逐项独立圆角卡片：图标+标题+副标题 | 右侧操作控件）。

**七个分段**（键归属）：

| 分段 | 内容 |
|------|------|
| 应用配置 | 路径 / 启动（默认启动页面 `startup_default_page`，2026-09-19）/ 日志高亮 / 配置文件（自工具页迁入） |
| 远程连接 | SSH/SFTP/FRP（为远程页预留落点） |
| 工具 | 快捷键与工具（连接诊断入口、前往工具页）+ AI 分析组（2026-09-07 自应用配置迁入） |
| 性能 | 亚克力 / 动画 / 表格平滑滚动（范围下拉 + 开关） |
| 数据库 | 数据源/双接口账号/MySQL（收集上传与手动添加已迁出） |
| 面板设置 | 售后（周期/自动刷新）+ 跑视频（署名）+ 运维（手动添加球桌记录 + 收集与上传组） |
| 外观 | 主题/字体/DPI/强调色 |

**信号**：`aftersale_cycle_saved`（周期保存 → 主窗口转发售后记录页刷新）、`aftersale_auto_refresh_changed`（自动刷新开关/间隔 → 即时启停定时器，2026-09-16）、`table_smooth_changed`（平滑开关 → 各 Hub 刷新）。

**日志高亮规则**：`log_highlight_rules` 列表 `[{name, pattern, color, notify}]`（引擎 `core/log_rules.py`），默认规则「错误」（红，通知）/「警告」（橙，静默）/「返回」「加分」「add」（旧版硬编码关键词迁移，橙，静默）；主窗口日志区实时匹配着色，`notify=True` 命中弹 InfoBar（每规则 10s 静默期）。

**NewLog 路径默认值**：`newlog_excel_dir` 默认 `~/Desktop/excel`、`newlog_out_dir` 默认 `~/Desktop`。

---

## main_window/ Hub 页面（二级界面，2026-09）

### main_window.pivot_page（Hub 基建：PivotPage / CardPage）

FluentWindow 单窗口重构后，原独立面板降层为「Pivot 二级导航 + 工作区」容器页的基建。主窗口导航顺序：**工作台 / 运维管理 / 售后 / 跑视频 / 远程 / 工具**，底部 **设置 / 关于**。

#### 类 `PivotPage`

Pivot 二级导航容器页基类（顶部横排 SegmentedWidget + 下方 QStackedWidget 工作区切换；实测嵌套 FluentWindow 会渲染异常，故用 Pivot 容器）。

| 方法 | 说明 |
|------|------|
| `addPage(page, text, icon)` | 注册子页面并生成 Pivot 项（objectName 同时作为 routeKey） |
| `switchTo(page)` | 容器内切换子页（与 FluentWindow.switchTo 同名兼容） |
| `pages()` | 已注册子页列表 |
| `lock_pivot_width()` | 按内容锁定切换条宽度（所有页面注册完成后调用一次） |
| `detach_workers()` | 请求停止所有子页后台线程（只 interrupt 不 wait，等待由主窗口统一做） |

#### 类 `CardPage`

卡片动作页：标题 + 说明 + 竖排动作按钮卡（远程会话/统计/设置/关于共用骨架）。

| 方法 | 说明 |
|------|------|
| `set_callbacks(callbacks)` | 绑定动作回调 |

### main_window.hub_popout（HubPopoutWindow 弹出面板窗口）

#### 类 `HubPopoutWindow`

通用「弹出面板」独立窗口（FluentWindow）：把无旧版独立窗口对应的 Hub（工具/远程）重新以独立 FluentWindow 打开，Hub 内 `self._win` 指向本窗口，故须代理宿主接口（`_show_info_bar` 等）；`videos_dir()` 代理视频目录、`closeEvent` 关闭时停后台 Worker。

### main_window.setting_cards（统一设置页卡片组件）

统一设置页的卡片式设置组件（2026-09-07 重构；视觉规格对齐售后运维面板成熟卡片样式 + Watt Toolkit 内联控件）。

#### 类 `SettingGroup(CardWidget)` / `SettingRow(CardWidget)` / `SubRow(QWidget)`

- `SettingGroup`：一组设置——组标题 + 逐项独立卡片；`addRow(row)` 追加设置卡、`addWidget(w)` 嵌入整卡组件（如 AdminSettingsPage / 周期设置卡 / 署名卡）
- `SettingRow`：单张设置卡片——图标 + 标题/描述 + 右侧内联控件区；`add_stretch_hint()` 允许长描述换行拉宽
- `SubRow`：折叠卡（ExpandSettingCard）内部的轻量行（标题 + 右侧控件）

#### 模块级工厂函数

| 函数 | 说明 |
|------|------|
| `make_switch(checked, on_change, text)` | Watt 式开关：左侧带「开/关」文字的 SwitchButton |
| `make_combo(items, index, on_change, width)` | 下拉选择：items=[(文本, data)]，on_change(data) |
| `make_spinbox(value, lo, hi, suffix, on_change, width)` | 内联数字调节（字号等）：先 setValue 再 connect，回显不误触发 |
| `make_button(text, on_click, icon, width, primary)` | 普通/主按钮 |
| `make_line_edit(value, on_change, placeholder, width, password)` | 内联文本/密码输入（弹窗配置域迁入统一设置页） |
| `make_path_row(key, title, value, mode, win, desc)` | 路径配置卡片行：LineEdit + 浏览按钮，编辑即存（config/paths.json） |

### main_window.hub_pages（业务 Hub）

业务域容器页（Hub）——把原独立 FluentWindow 面板降层嵌入主窗口：

| 类 | 导航 | 说明 |
|------|------|------|
| `ManagementHub(PivotPage)` | 「运维管理」 | 原运维面板六页面降层（球桌/设备状态/健康度/管理设置/控件测试/小游戏）；embedded 模式下 AdminSettingsPage 不建上传/添加/性能卡（getattr 守卫） |
| `AftersaleHub(PivotPage)` | 「售后」 | 售后面板降层（填写录入/记录与统计）；设置统一迁底部设置页；支持自动刷新（2026-09-16） |
| `LedgerHub(PivotPage)` | 「跑视频」 | 跑视频面板降层（填写录入/记录与统计） |
| `SettingsHubPage(QWidget)` | 底部「设置」 | 七分段统一设置页（见上文 SettingsHubPage 节） |
| `AboutPage(QWidget)` | 底部「关于」 | 版本信息与说明 |

### main_window.tool_hub（工具页 ToolHub）

`ToolHub(PivotPage)`：横排 Pivot 四项无图标（与运维/售后/跑视频同风格），四个工作区类：

| 工作区 | 类 | 说明 |
|------|------|------|
| 单杆视频 | `SingleVideoWork` | 参数卡（整行铺满，定宽下沉到控件）+ 右侧**日志预览面板**（只读终端、大小·行数、悬停完整路径、刷新按钮、256KB 截断、随选择联动）；参数区与「运行输出」终端间竖直 `QSplitter`（`_make_vsplitter`：handleWidth=2、无自定义 qss，外观对齐主界面工作台列间隔）；生成走 `workers/single_video_worker.SingleVideoWorker` |
| 端口占用 | `PortFakeWork` | 自写表格：TCP listen→LISTENING、UDP bind→BOUND；绑定地址用 `EditableComboBox` |
| 上传清单 | `UploadListWork` | 复选框文件表（表头浮 CheckBox 全选，`clicked` 接管三态；「删除所选」二次确认 + `_safe_upload_path` 越界拒绝）；勾选上传走 `ZipUploadWorker`（files=白名单） |
| 批量整理 | `NewLogWork` | NewLog 整理 + 打包上传，与 SingleVideoWork 同构（splitter/终端） |

工作区类：`SingleVideoWork(QWidget)`、`PortFakeWork(QWidget)`、`UploadListWork(QWidget)`、`NewLogWork(QWidget)`（上表左列即其实例挂载的 Pivot 项）。

busy 守卫共享 `_single_video_worker/_newlog_worker/_newlog_upload_worker`（任一在跑拒绝再启动）。配置键 `single_random_session_code`（默 True）/ `single_auto_open_dir`（默 False）位于 misc 域。辅助工厂 `_transparent()`（消工作区直角底色块）、`_make_terminal(parent, None)`（Expanding 终端，不设固定高）。

### windows.remote_session.remote_hub（RemoteHub 远程页）

#### 类 `RemoteHub`

`RemoteHub(PivotPage)`：Pivot 多视图——工作区类 `SessionWork(QWidget)`（会话总览：统计卡 + 7 列隧道表，行内 SSH/SFTP/RDP/断开/删除，SFTP 传输中二次确认）、`VisitorWork(QWidget)`（P2P 访客：注册只 persist 不拉 frpc，球桌号搜索联动带出 serverName）、`QualityWork(QWidget)`（连接质量：visitor RTT 探测样本）、`FrpsProxiesWork(QWidget)`（frps 代理清单：全类型代理表，xtcp 页签展示端口/版本列）、`TunnelConfWork(QWidget)`（隧道配置：frpc 服务器 + 进程控制 + 实时日志）。后端零改动复用 `core.frp_remote.get_session_manager()` 单例与 `core.frps_admin` 感知客户端；**构造不得拉起 frpc**；手动停 frpc 保注册表（close_all_sessions → records 暂存 → 全 remove → apply() 空表即停进程 → 重新 register → persist()）。连接诊断不属本页（设置-工具行开 `ConnDiagPanel` 独立弹窗）。

### main_window.update_mixin

#### 类 `UpdateMixin`

自动更新客户端编排层（S3）：纯逻辑在 `core/updater.py`（无 Qt），线程适配在 `workers/update_worker.py`，交互弹窗在 `windows/update_dialog.py`，导航按钮在 `main_window/update_nav.py`——本 Mixin 只做编排与 UI 反馈。

| 方法 | 说明 |
|------|------|
| `check_for_update(silent)` | 检查更新入口（关于页按钮 / 启动自动检查共用；silent=True 静默无 InfoBar） |
| `init_update_nav()` | 导航栏「设置」上方插入更新状态按钮（main_window 导航构建后调用） |
| `consume_update_receipt_on_startup()` | 启动后消费上次更新的回执（成功/失败都要给用户交代） |
| `auto_check_update_on_startup()` | 按配置 `update_auto_check` 决定启动是否静默自检 |

### main_window.update_nav

#### 类 `UpdateNavButton`

导航栏更新状态按钮（NavigationPushButton，不可选中路由，纯动作按钮；2026-09-20 需求：设置图标上方显示下载状态）。状态机由 UpdateMixin 驱动：hidden（隐藏）/ idle（可检查更新）/ checking（检查中转圈）/ downloading（下载中进度）。

| 方法 | 说明 |
|------|------|
| `state()` | 当前状态 |
| `set_state(state, tooltip)` | 切状态；state=hidden 时隐藏按钮 |

---

## win_api/ Windows API 层

### win_api.windows_api

Windows DLL 函数 ctypes 声明（仅 Windows 平台有效）。

#### 进程管理

| 函数 | 说明 |
|------|------|
| `win_suspend_process(pid)` | 挂起进程所有线程 |
| `win_resume_process(pid)` | 恢复进程所有线程 |
| `win_set_process_threads(pid, thread_action)` | 枚举进程全部线程逐个执行 thread_action（挂起/恢复句柄函数），上述两者的通用底座 |
| `get_process_name(pid)` | 按 PID 获取进程名（小写，失败返回空字符串），进程识别辅助 |

#### 显示设置

| 函数/常量 | 说明 |
|-----------|------|
| `_EnumDisplaySettingsW(device, mode, dm)` | 枚举显示设置 |
| `_ChangeDisplaySettingsW(dm, flags)` | 更改显示设置 |
| `DEVMODE` | Windows DEVMODEW 结构体（220字节） |
| `ENUM_CURRENT_SETTINGS` | 当前生效的显示模式 |
| `CDS_UPDATEREGISTRY` / `CDS_FULLSCREEN` | 显示设置标志 |

#### 窗口嵌入（RDP）

| 函数 | 说明 |
|------|------|
| `find_rdp_window_by_pid(pid, log)` | 按 PID 查找 RDP 会话窗口（评分制） |
| `find_rdp_session_window(log)` | 全局查找 RDP 会话窗口 |
| `find_mstsc_pids()` | 获取所有 mstsc.exe 进程 PID |
| `get_window_class_name(hwnd)` | 获取窗口类名 |
| `_SetParent_err(hwnd, parent)` | SetParent（带 GetLastError 捕获） |

---

## windows/tools/ 工具页功能后端

工具页（`main_window.tool_hub`）四工作区的运行时业务逻辑，由 `SingleVideoWorker` / `NewLogWorker` / `PortFakeWidget` 调用；
单杆渲染相关部分从 single_json 项目收编。包内另有 `newlog.py`（批量整理，见上 Workers 节）
与 `smoke_fluent_mainwindow.py`（GUI 冒烟脚本）。

### windows.tools.single_shot_video

单杆视频渲染服务（计分水印合成）。

| 符号 | 说明 |
|------|------|
| `SingleShotVideoServer` | 渲染服务封装：接收场次参数（日志路径/帧范围/输出目录）执行帧级计分提取与水印视频合成 |
| `build_bar_image(player)` | 比分条底图构建：选手0=模板原样；选手1=模板**整体镜像** + 品牌图形按 `BAR_BRAND_BOXES` 贴回未镜像像素（防 logo 翻反/箭头错位；`image_1.png` 已弃用） |
| `BAR_TEMPLATE_NAME` / `BAR_BRAND_BOXES` | 模板文件名（image_0.png）/ 品牌图形镜像还原框坐标 |
| `resource_path(rel)` | 打包/开发环境自适应资源路径（字体/模板等随包资源） |

> ⚠️ **禁止 cv2 HighGUI 调用**（`destroyAllWindows`/`imshow` 等）：运行环境 opencv 无 GUI 后端，调用抛异常导致「视频已写盘却报失败」。`tests/test_single_shot_bar.py` 用 AST 断言兜底。

### windows.tools.single_video_tool

单杆 json 生成工具。

| 函数 | 说明 |
|------|------|
| `generate_json(...)` | 按日志解析场次信息并生成单杆 json（帧级计分数据） |
| `extract_break(...)` | 开球局提取（从日志帧序列中定位有效局段） |

---

## p2p.py P2P 工具模块

| 函数 | 签名 | 说明 |
|------|------|------|
| `generate_random_port(exclude_ports=None)` | `(set?) -> int` | 生成随机端口（排除常用+已用端口） |
| `is_port_in_use(port, host='127.0.0.1')` | `(int, str) -> bool` | 检测端口是否被占用 |
| `open_xshell_and_xftp(host, port, ...)` | 使用 Xshell/Xftp 双开连接 | 外部工具调用 |

---

## Web 端接口（售后面板 Web）

售后面板 Web 端与桌面端售后页**同库同口径**（MySQL `autowork.aftersale_records`）。三套组成：后端 `web/aftersale_api/app.py`（FastAPI）、v1 前端 `web/aftersale_front`（Vue3+Vite）、v2 前端 `web/vue-pure-admin`（vue-pure-admin 7.0，售后页在 `src/views/aftersale/`，接口封装 `src/api/aftersale.ts`）。入口选择页 `web/aftersale_chooser/index.html`。

### 后端部署与开关（生产机 49.235.34.253）

| 项 | 值/说明 |
|------|------|
| 服务 | systemd `aftersale-web.service`（uvicorn 单 worker），凭据 `/opt/aftersale-web/.env`（chmod 600） |
| 环境变量 | `MYSQL_HOST/PORT/USER/PASS/DB`；`CYCLE_TYPE`（tue/mon/custom/month，默认 tue）+ `CYCLE_SPAN`；`WRITE_ENABLED`（默认 false）、`AUTH_ENABLED`（默认 false）、`AUTH_SECRET`（JWT 签名密钥） |
| 静态站根 | `/opt/aftersale-web/dist`：`index.html` 入口页｜`v1/index.html` 老系统（v1 资产留根 `assets/`）｜`v2/**` 新系统（base=`/v2/`） |
| 访问入口 | `http://49.235.34.253/`、`/v1/`、`/v2/`（80 端口与 newball.cloud 共端口 default_server 分流；8080 外部不可达）。**零 nginx 改动**：既有 `location / { try_files $uri $uri/ /index.html; }` 直接服务子目录 SPA |
| 认证前提 | `users.json`（bcrypt `pw_hash`）目前不存在——开 `AUTH_ENABLED` 前必须先补用户文件 |
| 部署脚本 | `tools/deploy/deploy_parallel_v1v2.py`（v1/v2 并行整包，幂等防覆盖 v1）、`tools/deploy/upload_aftersale_dist.py`（v1 产物）、`tools/deploy/deploy_aftersale_api.py`（后端+重启）；SSH 统一走 `tools/deploy/prod_ssh.py`，密码只从环境变量 `AFT_SSH_PASS` 读取 |
| v2 子路径三件套 | `VITE_PUBLIC_PATH=/v2/`、`useNav.getLogo()` 用 `import.meta.env.BASE_URL`、`public/platform-config.json` 的 Title |
| 验证脚本 | `web/aftersale_front/tools/verify_prod_layout.mjs`（产物预检）、`serve_dist_v2.mjs`（本地仿真）、`verify_prod_deployed.mjs` / `verify_prod_write.mjs`（线上巡检） |

> ⚠️ v2 的 `vite-plugin-fake-server` 生产 mock 走 xhook 注入，静态分析看不出、必须实测（正常产物应无 Service Worker、index.html 无额外 script 注入）。

### REST API（`web/aftersale_api/app.py`）

所有查询接口与桌面端 `database/aftersale_db` 口径一致：记录归属日期 = `substr(COALESCE(NULLIF(occurred_at,''),created_at),1,10)`；`resolved`/`is_initiative`/`is_our_problem` 为**中文字符串** `"是"`/`"否"`（非 0/1）；周期起点格式 `yyyy/MM/dd`，非当前模式合法起点 → 该筛选命中 0 条。

**只读接口（默认开放）**：

| 方法 路径 | 参数 | 说明 |
|------|------|------|
| `GET /api/health` | — | 健康检查，返回 `{ok, db}` |
| `GET /api/cycle-options` | — | 最近 12 个周期起点下拉（含 `current`），仅当前模式合法起点 |
| `GET /api/records` | `page, page_size(≤200), keyword, cycle_start, issue_type, resolved, is_initiative, is_our_problem` | 分页列表 + 同口径统计一次返回 `{total, rows, stats:{total,unresolved,initiative,our_problem}, page, page_size}` |
| `GET /api/table-columns` | — | 表格列定义（与桌面端 TABLE_COLUMNS 同构，前端据此渲染） |
| `GET /api/stats/charts` | 同 records 筛选（无 keyword） | 默认图表四件套：`region_dist`/`daily`（键名 `count`）/`our_problem`/`issue_type_dist`/`total`；无周期时取最近 90 天 |

**写入与高级接口（受开关控制，`WRITE_ENABLED=false` 一律 503）**：

| 方法 路径 | 说明 |
|------|------|
| `POST /api/auth/login` | 登录（需 `AUTH_ENABLED`）：bcrypt 校验 → 返回 12h JWT `{token, user}` |
| `GET /api/auth/me` | 校验 token（`AUTH_ENABLED` 关闭时直接放行） |
| `POST /api/records` | 新增（`_WRITABLE` 字段白名单；creator 缺省强制记为登录用户）→ `{id}` |
| `PUT /api/records/{id}` | 更新（可选乐观锁：带 `updated_at` 时条件更新，0 行 → 409 conflict） |
| `DELETE /api/records/{id}` | 删除 |
| `POST /api/records/batch-resolve` / `batch-delete` | 批量 `{ids:[...]}` → `{updated/deleted: n}` |
| `POST /api/stats/query` | 通用聚合（自定义图表）：`{dimension, measure(count/percent), chart(bar/line/pie/ring/hbar), sort, limit≤50, filter}`；维度白名单 `_DIM`（region/issue_type/resolved/is_initiative/is_our_problem/table_no/creator/resolver/day/week）防注入 |

认证接线：写接口经 `Depends(require_auth)`——`AUTH_ENABLED=false` 时不校验（桌面/内部场景放行），`true` 时要求 `Authorization: Bearer <token>`。

### 本地反代（`core.local_web_server`）

桌面程序启动时可选拉起本地 Web（默认 `http://localhost:8787`）：托管 v1 构建产物 + `/api/*` 反代云端 `http://49.235.34.253`（同一数据源）。配置键 `local_web = {enabled, port, api_base}`（misc 域）。纯标准库实现、daemon 线程、防路径穿越；独立测试 `python -m core.local_web_server --port 8787`。

---

## 配置门面 config/（原 settings.json，2026-09-06 拆分）

> **单文件 settings.json 已下线**（自动迁移为 `settings.json.bak` 兜底保留）。配置现按域拆分为 `config/` 目录 8 个域文件，唯一读写入口为 [`core.app_settings`](#coreapp_settings) 门面：按键自动路由、进程缓存 + RLock、`credentials`/`database` 两域落盘自动 DPAPI 加密。旧配置在应用首启自动分拣（幂等，只补缺键），升级用户无感。表结构头部注释/文档中出现的「settings.json 某键」一律按本表路由到对应域文件。

### 键 → 域文件路由总表

| 域文件 | 键 | 类型/默认值 | 说明 |
|--------|-----|------------|------|
| paths.json | `exe_dir` | str | SnookerTracking 程序目录 |
| paths.json | `videos_dir` | str | 视频/日志文件目录 |
| paths.json | `cipher_tool` | str | AES 解码工具路径 |
| paths.json | `front_exe` / `backend_exe` | str | 前端/后端程序路径 |
| paths.json | `newlog_excel_dir` / `newlog_out_dir` | str | NewLog 批量整理 Excel 目录 / 输出目录 |
| paths.json | `last_exe` | str | 上次选择的程序名 |
| paths.json | `single_pending_root` / `single_videos_root` | str | 单杆视频待处理/产出根目录 |
| paths.json | `newlog_target_name` | str | NewLog 整理署名，同时作为售后面板填写人默认值 |
| ui.json | `dpi_scale` | int = 100 | DPI 缩放百分比 |
| ui.json | `font_size` / `font_family` | int = 10 / `Microsoft YaHei UI` | 全局字号/字体 |
| ui.json | `theme_mode` | str = "auto" | 主题模式 auto/light/dark |
| ui.json | `dark_theme` | bool = false | 深色主题开关（旧字段，theme_mode 兼容回退） |
| ui.json | `classic_layout` | bool = false | 经典布局模式 |
| ui.json | `startup_default_page` | str = "homeInterface" | 启动默认展示页面（homeInterface / managementHub / aftersaleHub / ledgerHub / remoteHub / toolHub，非法值回退工作台；设置 → 应用配置 → 启动） |
| ui.json | `theme_color` | str = "#00BCD4" | 主题强调色（即时生效） |
| ui.json | `highlight_color` | [r,g,b] | 日志高亮颜色（旧字段，仅作 theme_color 兼容回退） |
| ui.json | `log_highlight_rules` | [object] | 日志高亮规则 `[{name, pattern, color, notify}]` |
| credentials.json 🔒 | `ssh_user` / `ssh_pass` | str | SSH 默认账号密码（密码 DPAPI 加密） |
| credentials.json 🔒 | `tcp_servers` | [str] | 保存的 TCP 服务器列表（ip:port） |
| credentials.json 🔒 | `sftp_default_remote_path` | str | SFTP 默认远程路径 |
| credentials.json 🔒 | `frpc_server` | object | frp 服务器配置（serverAddr/serverPort/auth_method/auth_token） |
| credentials.json 🔒 | `upload_host` / `upload_port` / `upload_remote_dir` | str/int | 上传 SFTP 服务器与远端目录 |
| credentials.json 🔒 | `upload_user` / `upload_pass` | str | 上传专用账号密码（不复用 SSH 凭据） |
| credentials.json 🔒 | `api_credentials` | object | 运维面板 API 配置（见下） |
| credentials.json 🔒 | `ai_vendor` | str = "deepseek" | AI 厂商标识（deepseek/qwen/kimi/zhipu/openai/gemini） |
| credentials.json 🔒 | `ai_model` | str | AI 模型名（空用厂商默认） |
| credentials.json 🔒 | `ai_api_keys` | object | 各厂商 API Key 字典（DPAPI 加密） |
| credentials.json 🔒 | `forensic_ai_analysis` | bool = true | 取证报告 AI 分析开关 |
| credentials.json 🔒 | `deepseek_api_key` | str | 旧版单厂商 Key（`ai_api_keys` 优先级更高，兼容保留） |
| database.json 🔒 | `mysql_sync` | object | MySQL 连接配置（见下） |
| database.json 🔒 | `data_retention` | object | 数据保留自动清理配置（见下） |
| aftersale.json | `aftersale_cycle` | object | 售后统计周期模式（见下） |
| aftersale.json | `aftersale_quick_phrases` | [str] | 售后录入页常用句（去重置顶） |
| aftersale.json | `aftersale_last_creator` / `aftersale_last_resolver` | str | 上次填写的填写人/解决人（预填记忆） |
| aftersale.json | `aftersale_cycle_recalc_pending` | bool | 重算待办标志（自愈机制内部键，勿手改） |
| perf.json | `perf_acrylic` / `perf_animation` | bool = true | 亚克力/动画性能开关 |
| perf.json | `perf_table_smooth` | bool = false | 全局表格平滑滚动（默认关） |
| perf.json | `perf_table_smooth_aftersale` / `_video` / `_management` / `_remote` | bool | 面板级平滑滚动覆盖（未设置回退全局） |
| perf.json | `perf_animation_aftersale` / `_video` | bool | 面板级动画覆盖 |
| perf.json | `performance_mode` | bool | 旧字段（首次读取后自动移除，迁移为前两项关闭） |
| remote.json | `remote_sessions` | [object] | 保存的远程会话列表 |
| remote.json | `restore_remote_sessions` | bool = false | 启动时自动恢复远程会话 |
| remote.json | `xtcp_secret_key` | str | XTCP 隧道密钥（frpc_token，DPAPI 加密） |
| misc.json | `web_port` | int = 8069 | 售后面板 Web 服务端口 |
| misc.json | `shortcut_*` | str | 快捷键配置（共 12 项，动态键兜底走 misc） |
| misc.json | `local_web` | object | 本地 Web 服务 `{enabled: true, port: 8787}`（缺省即启用） |
| misc.json | `ssh_commands` | [str] | SSH 终端常用命令条 |

🔒 = 加密域（落盘自动 DPAPI 加密、读取透明解密，密钥绑定当前 Windows 用户）。

### api_credentials 子结构（credentials.json，管理设置页维护）

| 字段 | 说明 |
|------|------|
| `active_source` | 启用的设备数据源：`kd`（默认）/ `xqzg` |
| `api1.username` / `api1.password` | 接口1 xqzg（Session 认证）账号密码 |
| `api2.username` / `api2.password` | 接口2 kd（JWT 认证）账号密码 |

### mysql_sync 子结构（database.json，MySQL 同步卡片维护）

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用（开启后 MySQL 完全替代本地 SQLite） |
| `host` / `port` | MySQL 服务器地址与端口（默认 3306） |
| `user` / `password` | 账号密码（密码 DPAPI 加密） |
| `database` | 数据库名（默认 autowork） |
| `auto_sync` | 已废弃（镜像推送机制 B 于 2026-08-23 下线，代码不再读取） |

### aftersale_cycle 子结构（aftersale.json，售后面板「统计周期设置」维护）

| 字段 | 说明 |
|------|------|
| `type` | 周期模式：`tue`（周二起，默认）/ `mon`（自然周）/ `custom`（自定义）/ `month`（自然月） |
| `start` | custom 模式的周期起始日（`yyyy-MM-dd`） |
| `span` | custom 模式的周期天数（≥1，默认 7） |

### data_retention 子结构（database.json，后台自动执行）

| 字段 | 默认值 | 说明 |
|------|--------|------|
| `enabled` | `true` | 总开关 |
| `age_days` | `60` | xqzg_status / kd_status 过期分区保留天数 |
| `check_interval_days` | `60` | 其余流水表的大小检查间隔（天） |
| `max_size_gb` | `3` | 触发按大小清理的表大小阈值（GB） |
| `min_size_gb` | `2` | 清理目标：删到低于该值停止（GB） |
| `min_keep_days` | `30` | 最低保留天数：最近 N 天数据永不删 |

清理范围：A = `xqzg_status` / `kd_status`；B = `aftersale_records` / `ledger_records` / `submission_log` / `health_alerts`（`tables` 白名单可过滤，未知表名忽略）。详见 [database.data_retention](#databasedata_retention)。

---

## 依赖方向

```
core ← win_api ← workers ← windows ← main_window ← main.py
                    ↑          ↑
                    database ──┘
```

严格单向依赖，禁止循环导入。`database/` 仅被 `windows/management_panel.py` 等上层模块引用。

**配置读取约定（2026-09-06 拆分后）**：各层统一经 `core.app_settings` 门面读写 `config/*.json`；**例外**：`database/backend.py` 与 `database/data_retention.py` 为避免导入 core 包触发 PySide6 依赖链，直接只读 `config/database.json`（不经门面）。
