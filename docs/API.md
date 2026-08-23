# AutoWork 接口文档

本文档描述 AutoWork 项目各模块的公开接口（类、函数、信号），供开发维护参考。

---

## 目录

- [core/ 基础层](#core-基础层)
- [workers/ 后台线程层](#workers-后台线程层)
- [database/ 数据层](#database-数据层)
- [windows/ 独立窗口层](#windows-独立窗口层)
- [main_window/ 主窗口层](#main_window-主窗口层)
- [win_api/ Windows API 层](#win_api-windows-api-层)
- [p2p.py P2P 工具模块](#p2ppy-p2p-工具模块)
- [配置文件 settings.json](#配置文件-settingsjson)

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

#### 函数 `qt_message_handler`

```python
def qt_message_handler(msg_type, context, message) -> None
```

Qt 消息处理器，将 Warning/Critical/Fatal 级别消息落盘。通过 `qInstallMessageHandler()` 注册。

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

低性能模式运行时开关（亚克力/动画即时生效，`settings.json` 持久化，兼容旧字段 `performance_mode`）。

| 函数 | 签名 | 说明 |
|------|------|------|
| `is_acrylic_enabled()` | `() -> bool` | 亚克力磨砂效果是否开启 |
| `is_animation_enabled()` | `() -> bool` | 界面动画是否开启 |
| `set_acrylic_enabled(enabled)` | `(bool)` | 设置亚克力开关（持久化 + 生效） |
| `set_animation_enabled(enabled)` | `(bool)` | 设置动画开关（持久化 + 生效） |
| `is_performance_mode()` | `() -> bool` | 低性能模式（两者均关闭） |
| `invalidate_cache()` | `()` | 使配置缓存失效（下次读取重载） |

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

| 方法 | 说明 |
|------|------|
| `open_session(kind, snk, table_id, notifier=None, source="")` | 建立远程会话（kind: ssh/sftp/rdp），自动确保 frpc 运行与隧道就绪 |
| `disconnect_visitor(server_name)` | 隧道面板「断开连接」：仅 frpc 运行中生效（返回 ok/not_running/not_found/error），先关相关会话再移除 visitor 释放端口，绝不自动启动 frpc |
| `delete_visitor(server_name)` | 隧道面板「删除 snk」：从注册表与持久化文件彻底移除，frpc 未运行时也可执行且不启动 frpc |
| `sessions_on_port(port)` / `is_transferring_on_port(port)` | 查指定本地端口上的会话面板 / 是否有 SFTP 传输进行中 |
| `close_sessions_on_port(port, reason)` / `close_all_sessions(reason)` | 优雅关闭指定端口/全部会话面板（panel.shutdown()），返回关闭数量 |
| `shutdown()` | 停止 frpc 并关闭全局会话窗口（主窗口 closeEvent 调用） |

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
| `migrate_settings_file(path=None)` | `(str?) -> bool` | 启动时自动迁移：明文敏感字段加密回写一次（幂等） |

敏感字段集合：顶层键（`ssh_pass` 等）+ 嵌套路径（`api_credentials.api1.password` 等）统一由 `SENSITIVE_KEYS` / `NESTED_SENSITIVE_PATHS` 维护。

---

### core.version

应用版本号：基于 git 分支与提交次数自动计算。

| 常量/函数 | 签名 | 说明 |
|-----------|------|------|
| `BASE_VERSION` | `str = "2.8"` | 主.次版本（手工维护，新增功能集 → 次版本 +1） |
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

---

## workers/ 后台线程层

所有 Worker 均继承 `QThread`，通过 Qt 信号与 GUI 线程通信。

### TCPWorker

TCP/SSH 连接验证工作线程。

```python
TCPWorker(host: str, port: int, username: str, password: str)
```

| 信号 | 类型 | 说明 |
|------|------|------|
| `result_ready` | `Signal(str)` | 连接成功，返回 `hostname && whoami` 输出 |
| `error` | `Signal(str)` | 连接失败，返回中文错误描述 |

| 方法 | 说明 |
|------|------|
| `run()` | 执行连接（自动在 finally 中关闭 client） |
| `close()` | 手动关闭 paramiko client + transport |

---

### SFTPConnectWorker

异步建立 paramiko.Transport 连接（含自动重试）。

```python
SFTPConnectWorker(host: str, port: int, username: str, password: str)
```

| 信号 | 类型 | 说明 |
|------|------|------|
| `connected` | `Signal(object)` | 成功，发射 `paramiko.Transport` 对象 |
| `error` | `Signal(str)` | 最终失败，返回中文错误 |

| 方法 | 说明 |
|------|------|
| `abort()` | 请求中止重试循环 |

---

### SFTPListWorker

异步 SFTP 列目录（使用 `listdir_attr` 单次网络往返）。

```python
SFTPListWorker(transport: paramiko.Transport, remote_path: str)
```

| 信号 | 类型 | 说明 |
|------|------|------|
| `result` | `Signal(str, list)` | (路径, 条目列表)。条目为 dict：`{name, is_dir, size, mtime, perm}` |
| `error` | `Signal(str)` | 列目录失败 |

---

### SFTPOperationWorker

异步 SFTP 文件操作（上传/下载/删除/创建目录/重命名/创建文件），支持进度、暂停、取消。

```python
SFTPOperationWorker(conn_params: tuple, operation: str,
                    local_path='', remote_path='', file_size=0)
```

**operation 取值**：`'upload'` | `'download'` | `'delete'` | `'rmdir'` | `'mkdir'` | `'rename'` | `'create_file'`

| 信号 | 类型 | 说明 |
|------|------|------|
| `success` | `Signal(str)` | 操作成功消息 |
| `error` | `Signal(str)` | 操作失败消息 |
| `progress` | `Signal(int, int)` | (已传输字节, 总字节) |

| 方法 | 说明 |
|------|------|
| `pause()` | 暂停传输 |
| `resume()` | 恢复传输 |
| `stop()` | 取消传输（抛出 InterruptedError） |

---

### SFTPDirTransferWorker

异步 SFTP 目录递归传输（整目录上传/下载）。

```python
SFTPDirTransferWorker(conn_params: tuple, operation: str,
                      local_dir='', remote_dir='', dir_name='')
```

**operation 取值**：`'upload_dir'` | `'download_dir'`

| 信号 | 类型 | 说明 |
|------|------|------|
| `success` | `Signal(str)` | 传输完成（含文件数统计） |
| `error` | `Signal(str)` | 传输失败/部分失败 |
| `progress` | `Signal(int, int)` | (已传输字节, 总字节) |

| 方法 | 说明 |
|------|------|
| `pause()` / `resume()` / `stop()` | 传输控制 |

---

### SSHConnectWorker

异步建立 SSH 连接（保持 client 存活，含自动重试）。

```python
SSHConnectWorker(host: str, port: int, username: str, password: str)
```

| 信号 | 类型 | 说明 |
|------|------|------|
| `connected` | `Signal(object)` | 成功，发射 `paramiko.SSHClient` 对象 |
| `error` | `Signal(str)` | 最终失败 |

| 方法 | 说明 |
|------|------|
| `abort()` | 请求中止重试循环 |

---

### SSHExecWorker

异步执行 SSH 命令（exec_command 模式，无持久 shell）。

```python
SSHExecWorker(client: paramiko.SSHClient, command: str)
```

| 信号 | 类型 | 说明 |
|------|------|------|
| `output` | `Signal(str)` | 标准输出内容 |
| `error` | `Signal(str)` | 标准错误 / 异常信息 |
| `done` | `Signal()` | 命令执行完毕 |

---

## workers/table_worker.py 球桌/设备数据 Worker

球桌与设备数据 API 异步请求 Worker（均继承 `QThread`），账号密码统一从 `settings.json` 的 `api_credentials` 节点读取。

#### 模块级函数与常量

| 函数/常量 | 签名 | 说明 |
|-----------|------|------|
| `_load_api_credentials()` | `() -> dict` | 读取 `settings.json` 的 `api_credentials` 配置 |
| `get_active_api_source()` | `() -> str` | 当前启用的设备数据源（`'kd'`/`'xqzg'`，默认 kd） |
| `build_image_path(file_path, device_code, category)` | `(str, str, str) -> str` | 构造迁移路径 `media/{日期}/{设备码}/{分类目录}/` |
| `CATEGORY_DIRS` | `dict` | 中文分类 → 服务器目录名（正常=normal / 操作=except / 待处理=pending / 使用=operation / 精度=accuracy / 问题=already / 废弃=rubbish） |
| `DIR_CATEGORIES` | `dict` | `CATEGORY_DIRS` 的反向映射 |

### TableFetchWorker

拉取球桌列表（wechat2-billiard.newbv.cn，无认证，pageSize=1000 一次拉完写入本地库）。

```python
TableFetchWorker()
```

| 信号 | 类型 | 说明 |
|------|------|------|
| `result_ready` | `Signal(list)` | 全量球桌数据列表 |
| `error` | `Signal(str)` | 错误信息 |

### SnookerOmFetchWorker

拉取接口1（xqzg.newbv.cn）设备状态数据，Session + CSRF 认证，401/403 自动重登录重试一次。响应数据在 `results` 键。

```python
SnookerOmFetchWorker(file_path="", page=1, pagesize=1000,
                     username=None, password=None)
```

| 信号 | 类型 | 说明 |
|------|------|------|
| `result_ready` | `Signal(dict)` | 完整 JSON（含 total / results / summary_row） |
| `error` | `Signal(str)` | 错误信息 |

### DevicesFetchWorker

拉取接口2（kd.newbv.cn:30005）设备状态数据，JWT Bearer Token 认证（登录端点 `/api/getAccessToken/`），401 自动重登录重试一次。响应数据在 `lists` 键，`file_path` 参数为日期分区（如 `2026/08/02`）。

```python
DevicesFetchWorker(file_path="", page=1, pagesize=1200,
                   username=None, password=None)
```

| 信号 | 类型 | 说明 |
|------|------|------|
| `result_ready` | `Signal(dict)` | 完整 JSON（含 lists） |
| `error` | `Signal(str)` | 错误信息 |

### MigrateImageWorker

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

| 信号 | 类型 | 说明 |
|------|------|------|
| `success` | `Signal(int)` | 成功迁移的图片数量 |
| `error` | `Signal(str)` | 错误信息（含失败文件列表摘要） |
| `progress` | `Signal(int, int)` | (当前进度, 总数) |

### LoginTestWorker

测试 API 登录是否可用（管理设置页「测试连接」按钮）。

```python
LoginTestWorker(api_name, username=None, password=None)
# api_name: "api1"（xqzg）或 "api2"（kd）
```

| 信号 | 类型 | 说明 |
|------|------|------|
| `success` | `Signal(str)` | 成功提示 |
| `error` | `Signal(str)` | 失败原因 |

### SingleVideoWorker

单杆视频生成工作线程（工具菜单「单杆视频」）。日志解析（帧级计分提取）、视频水印合成均在子线程执行，逐行进度通过信号回传。

```python
SingleVideoWorker(params: dict)
```

| 信号 | 类型 | 说明 |
|------|------|------|
| `line` | `Signal(str)` | 处理进度日志（追加到对话框输出区） |
| `finished_ok` | `Signal(str)` | 生成成功，返回视频路径 |
| `error` | `Signal(str)` | 生成失败，返回错误首行 |

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

### FileCopyWorker

异步文件拷贝（`shutil.copy2` 的线程替代）。

```python
FileCopyWorker(src, dst)
```

| 信号 | 类型 | 说明 |
|------|------|------|
| `copy_finished` | `Signal()` | 拷贝成功完成 |
| `error` | `Signal(str)` | 拷贝失败 |

### CollectFilesWorker

异步收集设备视频/日志/CPP 日志/detect.bin 到 `upload` 工作区。已存在的目标文件直接跳过（重复点击不重复复制）。

```python
CollectFilesWorker(videos_dir, device_id, base_names)
```

| 信号 | 类型 | 说明 |
|------|------|------|
| `done` | `Signal(str, int, list)` | (设备目录名, 实际复制文件数, 缺失项说明列表) |
| `error` | `Signal(str)` | 错误信息 |

### ZipUploadWorker

打包 upload 目录为 zip → SFTP 上传 → 清空本地 upload 目录。凭据用上传专用字段（不复用 SSH 凭据），支持取消（取消后自动清理临时 zip）。

```python
ZipUploadWorker(upload_root, host, port, username, password,
                remote_dir, parent=None, content_root=None,
                zip_prefix="upload", zip_dir=None, remove_zip_after_done=False)
```

| 信号 | 类型 | 说明 |
|------|------|------|
| `progress` | `Signal(str)` | 阶段提示（打包中/连接中/上传中） |
| `percent` | `Signal(int)` | 上传字节进度 0-100 |
| `done` | `Signal(str)` | 成功信息（zip 名与远端路径） |
| `error` | `Signal(str)` | 错误信息 |
| `cancelled` | `Signal()` | 用户取消完成（临时 zip 已清理） |

---

## workers/newlog_worker.py 批量整理 Worker

### NewLogWorker

后台运行 NewLog 批量整理主流程（按 Excel 署名筛选，批量归类视频/日志/配置文件）。

```python
NewLogWorker(target_name)
```

| 信号 | 类型 | 说明 |
|------|------|------|
| `line` | `Signal(str)` | 逐行运行日志（临时 Handler 转发 NewLog 模块 logger 输出） |
| `finished_ok` | `Signal(str)` | 整理完成，返回输出目录 |
| `error` | `Signal(str)` | 运行失败 |

---

## workers/aftersale_worker.py 售后数据 Worker

### AftersaleDBWorker

通用后台 DB 操作 Worker：将 `aftersale_db` / `table_db` 的同步 DB 操作移到工作线程，避免阻塞 GUI。

```python
AftersaleDBWorker(func, *args, **kwargs)
```

| 信号 | 类型 | 说明 |
|------|------|------|
| `result_ready` | `Signal(object)` | 查询/保存结果（**不能命名为 finished，会遮蔽 Qt 原生 finished**） |
| `error` | `Signal(str)` | 异常信息 `类型名: 描述` |

**保活与清理机制**（防频繁刷新时旧 worker 被 GC 销毁导致 `QThread: Destroyed while thread is still running` 崩溃）：
- 模块级 `_running` 集合强引用，线程退出前不被 GC
- 用 Qt **原生** `finished` 信号挂 `_release`（run() 返回后由 Qt 发射，线程已标记结束，销毁安全）
- `run()` 内用 `isInterruptionRequested()` 丢弃过期结果（新查询取代旧查询时不回调）

> 坑：PySide6 中 `super().finished` 会被子类同名信号遮蔽，不能用于此目的；自定义信号必须避开 QThread 原生信号名（finished/started）。

---

## workers/mysql_sync_worker.py MySQL 同步 Worker

### MysqlSyncWorker

异步推送本地 SQLite 数据到远程 MySQL。

```python
MysqlSyncWorker(table_name=None, parent=None, cfg=None)
```

- `table_name` 为空推全部表；`"aftersale_records"` 走售后业务键 upsert；其他表名单独推送
- `cfg`：显式连接配置（「立即同步」用表单最新值，避免读旧配置）

| 信号 | 类型 | 说明 |
|------|------|------|
| `progress` | `Signal(str)` | 阶段进度（如 "billiard_tables: 120 条已推送"） |
| `success` | `Signal(int, str)` | 成功（总条数, 耗时描述） |
| `error` | `Signal(str)` | 失败信息 |

### MysqlTestWorker

异步测试 MySQL 连接。

```python
MysqlTestWorker(cfg, parent=None)
```

| 信号 | 类型 | 说明 |
|------|------|------|
| `finished` | `Signal(bool, str)` | (是否成功, 描述) |

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
| `save_xqzg(rows)` / `query_xqzg_page(page_no, page_size, keyword="", order_by="", desc=False, include_files=False)` | xqzg 数据存取（无日期分区，数据在 `results` 键；`include_files=True` 时返回 8 类文件清单反序列化，列表页默认轻量模式） |
| `save_kd(rows, file_path="")` / `query_kd_page(page_no, page_size, keyword="", file_path="", order_by="", desc=False, include_files=False)` | kd 数据按日期分区存取（自动序列化/反序列化文件列表字段）；`include_files=False` 时轻量查询不含 8 类文件 JSON |
| `upsert_kd(rows, file_path="")` | 按 `(file_path, device_code)` 增量更新/插入（keyword 搜索拉取专用，不覆盖同日期其他设备） |
| `get_kd_row_full(row_id)` / `get_xqzg_row_full(row_id)` | 按 id 查完整行（含文件清单反序列化，配合轻量列表页懒加载） |
| `get_kd_dates()` | 获取本地已有的 kd 日期分区列表（降序） |

kd_status 历史分区保留 60 天（`_KD_KEEP_DAYS`），每次保存后自动清理过期分区。

**两数据源字段对照（同套接口字段，无独有字段）**：

接口1（xqzg，`https://xqzg.newbv.cn/api/snooker_om/status/`）与接口2（kd，`http://kd.newbv.cn:30005/api/devices/status/`）返回同套字段集（约 50 个）：`device_code` / `room_id` / `table_id` / `club_name` / `status` / `address` / `local_code` / 各类计数 / `target_directory` / `normal_total` / `pic_total` / 8 类文件清单（`normal_files`…`version_files`）/ `region` / `error_rate` / `operation_rate` 等。`xqzg_status` 与 `kd_status` 表均按「13 个统计字段（`STATUS_FIELDS`）+ 10 个扩展字段（`KD_EXTRA_FIELDS`：`device_code`、`target_directory`、`status`、8 类文件清单）」落库，文件清单 JSON 序列化；FTS 搜索字段 = 统计字段 + `device_code`。

**设计上就不同的点（非 bug，勿按缺陷处理）**：

| 差异 | kd（接口2） | xqzg（接口1） |
|------|------|------|
| 数据组织 | 按日期分区快照（`file_path`=`2026/08/02`），历史保留 60 天 | 全量快照、无日期分区，面板日期选择器禁用 |
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
| `is_mysql_test_mode()` | `() -> bool` | MySQL 测试模式是否开启（读 `settings.json` → `mysql_sync.enabled`） |
| `create_mysql_connection()` | `() -> MysqlConnectionAdapter` | 创建 MySQL 连接适配器；pymysql 未安装抛 RuntimeError。关键参数：`autocommit=True`（QThread 结束后 thread-local 连接被丢弃，若留未提交事务会持元数据锁级联卡死）、读写超时 60s |
| `convert_placeholders(sql)` | `(str) -> str` | SQLite 占位符 `?` → MySQL `%s`（跳过字符串字面量内的 `?`） |
| `convert_on_conflict(sql)` | `(str) -> str` | `ON CONFLICT(col) DO UPDATE SET ...=excluded.x` → `ON DUPLICATE KEY UPDATE ...=VALUES(x)` |
| `convert_insert_or_replace(sql)` | `(str) -> str` | `INSERT OR REPLACE` → `INSERT`（MySQL 无此语法） |
| `MYSQL_DDL` | `dict` | 8 张表的 MySQL 建表语句（IF NOT EXISTS 幂等，与 SQLite DDL 一一对应） |

**类 `MysqlConnectionAdapter`**：模拟 `sqlite3.Connection` 接口。`execute`/`executemany` 自动套用全部方言转换；`PRAGMA` 静默跳过；`executescript` 按分号拆条执行；附 `column_exists` / `table_exists`（替代 `PRAGMA table_info`）。

**类 `MysqlCursorAdapter`**：模拟 `sqlite3.Cursor` 接口（`fetchone`/`fetchall`/`description`/`rowcount`/`lastrowid`/可迭代）。

方言转换还涵盖：SQLite `date()` 函数 → `DATE_SUB/DATE_FORMAT`、去除 `COLLATE NOCASE`、`sync_meta.key/value` 保留字加反引号。

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

#### 周期计算（可配置模式）

周期模式：`tue`=周二起（默认）/ `mon`=自然周（周一起）/ `custom`=自定义起始日+周期天数。配置存于 `settings.json` 的 `aftersale_cycle` 节点。

| 函数 | 签名 | 说明 |
|------|------|------|
| `load_cycle_mode()` | `() -> dict` | 读取周期模式 `{type, start, span}`，缺省/非法回退周二起 |
| `save_cycle_mode(mode)` | `(dict) -> dict` | 合并写周期配置（保留 settings.json 其余字段），返回规范化配置 |
| `cycle_span_days()` | `() -> int` | 当前模式周期天数：tue/mon 固定 7，custom 取配置值（≥1） |
| `cycle_start_of(dt)` | `(datetime) -> str` | 计算给定时间所属周期起始日（`yyyy/MM/dd`），按当前模式分发 |
| `current_cycle_start()` | `() -> str` | 当前周期起始日 |
| `cycle_label(cycle_start)` | `(str) -> str` | 周期展示标签 `08/18 - 08/24`（起始日 + span-1 天） |

**周期归属统一按记录发生时间动态计算**：列表筛选、统计、周期下拉、导出四处共用 `_record_cycle(occurred_at, created_at)`（occurred_at 缺失/非法时回退 created_at）→ `cycle_start_of`，不依赖冗余落库的 `cycle_start` 字段（该字段仅作导出展示，可能因周期配置变更与实际归属不一致）。SQL 侧只过滤类型/状态/关键词，周期在 Python 侧按同一规则过滤，保证列表与统计口径一致。

#### 增删改

| 函数 | 签名 | 说明 |
|------|------|------|
| `insert_record(record)` | `(dict) -> int` | 新增记录返回 id。created_at 取填写时刻；occurred_at 缺省取当日；cycle_start 按发生时间归属（缺失回退填写时间）；snk_code/device_code 未提供时按桌号精确匹配球桌库自动带出 |
| `update_record(record)` | `(dict) -> int` | 按 id 更新（created_at 保留原值），cycle_start 缺失时按发生时间重算 |
| `delete_record(rec_id)` | `(id) -> int` | 按 id 删除，返回受影响行数 |

#### 查询

| 函数 | 签名 | 说明 |
|------|------|------|
| `query_page(page_no, page_size, keyword="", cycle_start="", issue_type="", resolved="")` | `-> (total, rows)` | 分页查询。周期在 Python 侧按记录时间动态归属（`_match_cycle`），SQL 仅过滤类型/状态/关键词 |
| `query_with_stats(...)` | `-> (total, rows, stats)` | 分页 + 同口径统计。stats 不带 resolved 筛选（避免已解决/未解决计数退化），返回 `{total, resolved, unresolved}` |
| `get_cycle_options()` | `() -> list` | 周期下拉选项：库中记录实际归属周期（按 occurred_at 动态计算）去重降序。**仅返回确有数据的周期，不额外插入库中不存在的当前周期** |
| `get_field_candidates()` | `() -> dict` | 动态候选 `{problems, resolvers, regions}`（按使用频次降序各取前 60），问题候选为空时合并预置常见项 |

#### 导出 / 导入

| 函数 | 签名 | 说明 |
|------|------|------|
| `export_xlsx(path, keyword="", cycle_start="", issue_type="", resolved="")` | `-> int` | 按筛选条件导出全部记录为 xlsx，返回条数。表头与售后汇总 Excel 对齐，附加填写时间/填写人/周期列；周期列按发生时间动态归属展示 |
| `parse_excel_rows(xlsx_path)` | `-> (headers, rows)` | 解析售后汇总 Excel（不写库），供导入预览与正式导入共用。表头按中文名定位，类型列分组首行向下填充，空行跳过，是否解决默认「否」，缺必需列抛 ValueError |
| `import_excel_rows(xlsx_path)` | `-> int` | 一次性导入历史 Excel，返回导入条数（内部调 `parse_excel_rows` 后批量写库） |

---

### database.mysql_sync

MySQL 远程镜像同步层（SQLite → MySQL 单向推送）。SQLite 始终为主读写库，本模块只做单向推送，绝不反向写入 SQLite；pymysql 不可用或连接失败时静默降级。首次连接自动建库建表（幂等）。

| 函数 | 签名 | 说明 |
|------|------|------|
| `test_connection(cfg=None)` | `-> (ok, msg)` | 测试 MySQL 连接是否可达（先无 database 连接测基础连通性） |
| `ensure_schema(cfg=None)` | `-> (ok, msg)` | 确保远程库与表结构就绪（幂等，含旧库补列迁移） |
| `push_all(progress_cb=None)` | `-> (ok, msg, total)` | 全量推送 5 张运维业务表（billiard_tables/xqzg_status/kd_status 全量覆盖，submission_log 增量追加，device_mapping 全量覆盖），记录 last_push_time |
| `push_table(table_name, progress_cb=None)` | `-> (ok, msg, count)` | 按表名单独推送 |
| `push_aftersale(cfg=None, progress_cb=None)` | `-> (ok, msg, count)` | 售后记录安全推送：业务键 `(created_at, creator, table_no, problem)` 去重 upsert（不 TRUNCATE 不按 id 去重）。多用户共享库全量 replace 会清掉他人数据，两侧 id 各自增长会撞车；不存在则 INSERT、存在则 UPDATE，本地删除不反向删除远程 |
| `is_enabled()` | `() -> bool` | MySQL 同步是否已启用 |
| `get_last_push_time()` | `() -> str` | 从 MySQL 读取上次推送时间，未推送过/连接失败返回空串 |

推送策略：`_push_replace`（TRUNCATE + INSERT 全量覆盖）/ `_push_upsert`（ON DUPLICATE KEY UPDATE 按主键去重）/ `_push_insert_ignore`（INSERT IGNORE 仅追加）。`_read_sqlite` 按实际列与目标列取交集读取，本地老库缺列补空串占位。

---

## windows/ 独立窗口层

### SFTPWindow

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

---

### SSHTerminalWindow

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

---

### RDPWindow

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

---

### SingleVideoDialog

单杆视频参数对话框（工具菜单「单杆视频」，`windows/single_video_dialog.py`）。继承 `MessageBoxBase`（透明模态窗口），由主窗口 `UiMixin._on_open_single_video` 注入 `_start` 回调后 `exec()` 打开。

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

### RemoteSessionWindow

远程会话窗口（`FramelessWindow`），展示 frpc 日志与隧道状态，由 `RemoteSessionManager` 统一管理。

---

### TunnelPanelWindow

「当前隧道」面板（`FramelessWindow`），主窗口远程面板入口打开，展示全局活跃隧道。

---

### ConnDiagPanel

连接诊断面板（`QDialog`），网络连通性诊断。

---

### MoyuReaderWidget

摸鱼阅读器（`QWidget`），内置文本阅读（TXT/粘贴）、网页正文抓取、2048/贪吃蛇小游戏。

---

### ManagementPanelWindow

运维管理面板（`windows/management_panel.py`），qfluentwidgets `FluentWindow` + 左侧导航，六个功能页面。由主窗口「球桌管理」按钮打开（`UiMixin._on_open_table_panel`），支持 `python -m windows.management_panel` 独立调试。

```python
ManagementPanelWindow(parent=None)
```

**页面构成**：

| 页面 | 类 | 说明 |
|------|------|------|
| 球桌管理 | `TablePage` | wechat2-billiard 球桌数据：表格/搜索/分页/列筛选/右键复制/手动添加记录；含 `code`（设备编码）列，默认隐藏可在「筛选」菜单勾选显示（`_hidden_cols = {在线状态, 设备编码}`） |
| 设备状态 | `DevicePage` | kd / xqzg 数据源切换（`get_active_api_source()`），按日期分区查看；集成图片迁移 |
| 设备健康度管理 | `HealthPage` | 健康度异常告警：每 30 分钟全量拉取 health（`TableFetchWorker` → `sync_health_alerts` 落库），每 1 小时重载展示；阈值 4000/5000/40 万；支持标记已处理 |
| 管理设置 | `AdminSettingsPage` | 数据源选择（kd/xqzg）、双接口账号密码、测试连接，合并写入 `settings.json` |
| 小游戏 | `GamePage` | 摸鱼中心（2048/贪吃蛇等） |
| （隐藏）健康趋势 | `TrendPage` | 健康度趋势看板（C3）：突增预警 + 单设备趋势折线 + TOP N 排行，仅 kd 数据源可用；导航入口已注释隐藏，恢复取消注释即可 |

**图片迁移交互（DevicePage）**：

- `总数`(pic_total) / `正常`(normal_count) / `操作`(except_count) 三列为链接色可点击单元格（`_FILE_VIEW_FIELDS`）
- 点击后右侧滑出 `FileListPanel`（QPropertyAnimation，宽 360，需 `WA_StyledBackground` 才不透明）展示 [分类, 文件名]
- 点击文件条目弹 RoundMenu 四选项（问题/精度/使用/废弃，`MIGRATE_DEST_OPTIONS`）→ `DevicePage.migrate_file()` → `MigrateImageWorker` 迁移单文件 → 成功后 `_silent_refresh()` 静默重拉刷新

| 关键方法 | 说明 |
|------|------|
| `_on_cell_clicked(row, col)` | 单元格点击 → 打开文件面板 |
| `migrate_file(fname, src_cat, dest_cat)` | 发起单文件迁移 |
| `_silent_refresh()` | 迁移后静默重拉当前数据源 |

**模块级辅助**：`_load_settings()` / `_save_settings(data)`（settings.json 合并读写）、`_copy_table_selection(table)`（表格选中内容复制）、`FILE_FIELD_CATEGORIES`（文件字段 → 中文分类）。

---

### AftersalePanelWindow（售后面板）

`windows/aftersale_panel.py`：售后面板（qfluentwidgets `FluentWindow` + 左侧导航，风格仿运维管理面板）。由主窗口 `_on_open_aftersale`（单例复用）或运维面板入口打开，支持 `python -m windows.aftersale_panel` 独立调试。数据层 `database/aftersale_db.py`，所有 DB 读写经 `AftersaleDBWorker` 后台线程，UI 零阻塞。

```python
AftersalePanelWindow(parent=None)
```

**页面构成**：

| 页面 | 类 | 说明 |
|------|------|------|
| 填写录入 | `EntryPage` | 售后问题登记表单（`AftersaleForm`），提交后写库并通知记录页刷新 |
| 记录与统计 | `RecordsPage` | 筛选（周期/类型/状态/关键词）+ 分页 + 统计 + 编辑/删除/导出 xlsx/导入 Excel |
| 设置 | `SettingsPage` | 统计周期设置（`CycleSettingsPage`）+ 数据库设置（`MysqlSyncCard` sync_scope="aftersale"） |

**共享表单 `AftersaleForm`**（录入页与编辑弹窗复用）：字段与售后汇总 Excel 对齐 + 系统附加字段。球房输入防抖搜索球桌库，候选点选/唯一命中自动带出桌号/SNK/城市；发生时间步进按钮补录历史日期。

| 方法 | 说明 |
|------|------|
| `load_candidates(cands)` | 填充动态候选（问题/解决人/地区），保留已输入文本 |
| `set_values(rec)` / `collect()` / `validate()` / `clear_form()` | 编辑回填 / 收集值 / 必填校验 / 清空 |

**周期筛选**（`RecordsPage`）：周期下拉选项来自 `get_cycle_options()`（库中记录实际归属周期）；当前周期仅在库中有数据时出现。切换周期后列表与统计按同一套归属规则重查，一一对应。

| 关键方法 | 说明 |
|------|------|
| `_load_cycles_then_data()` | 先异步拉周期选项填充下拉，再加载数据 |
| `_on_cycles_loaded(cycle_starts)` | 填充周期下拉（当前周期仅有数据时显示），默认选中当前周期否则全部周期 |
| `_load()` | 按当前筛选异步查询（分页 + 统计一次返回） |
| `set_keyword(kw)` | 球桌管理右键跳转：按桌号预筛选，周期放宽为全部 |

**弹窗**：`EditRecordDialog(MessageBoxBase)` 编辑记录（复用共享表单）；`ImportPreviewDialog(QDialog)` 导入预览（字段要求提示 + 前 20 行解析效果 + 确认导入）。`FluentCombo` 为原生 QComboBox + Fluent 统一样式（主题自适应 + 自绘下拉箭头，支持 setEditable/findData）。

**周期设置 `CycleSettingsPage`**：统计周期模式单选（周二起默认/自然周/自定义起始日+天数），保存写 settings.json 并 `saved` 信号通知记录页刷新周期下拉与统计。

---

### MysqlSyncCard（MySQL 同步配置卡片）

`windows/mysql_sync_card.py`：可复用 MySQL 同步配置卡片（运维面板 / 售后面板共用）。连接表单 + 启用/自动同步开关 + 测试连接/保存配置按钮。配置读写 DPAPI 加密落盘，以磁盘最新内容为 base 合并写（防双缓存覆盖）。

```python
MysqlSyncCard(parent=None, sync_scope="ops")
```

- `sync_scope="ops"`：推 5 张运维业务表，卡片标题「MySQL 远程同步」
- `sync_scope="aftersale"`：只推售后记录，卡片标题「数据库设置（MySQL）」

MySQL 开启时实时读写远程库（本地 SQLite 仅作降级兜底），不再需要手动「立即同步」；启用开关从关变开时自动后台推送本地历史数据到远程（`_auto_sync_history`，降级期间增量由 merge_back 自动合并）。

| 方法 | 说明 |
|------|------|
| `load()` | 从 settings.json 加载当前配置填充表单 |
| `_on_test()` / `_on_save()` | 异步测试连接 / 合并写配置即时生效（启用时自动同步本地历史） |

---

### ForensicReportPanel（SSH 故障取证包）

`windows/tunnel/forensic_report.py`：SSH 连接失败时一键生成诊断取证包（模块级函数 + `ForensicWorker` 后台线程）。

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

**类 `ForensicWorker(QThread)`**：后台逐条执行诊断命令组并生成报告（exec_command 独立 channel，不阻塞交互）。

| 信号 | 类型 | 说明 |
|------|------|------|
| `line` | `Signal(str)` | 逐条命令执行进度 |
| `done` | `Signal(str)` | 报告文件路径 |
| `error` | `Signal(str)` | 失败信息 |

---

### ImageViewerDialog

`windows/management/image_viewer.py`：设备状态图片查看卡片对话框（左右键翻页，支持分类迁移）。

```python
ImageViewerDialog(entries, index, file_path, device_code, device_page,
                  can_migrate=True, dest_options=(), btn_qss=None, parent=None)
```

- `_ImageFetchWorker`：后台下载图片字节流（静态资源无需认证头，可 cancel）
- `_image_urls(fname, src_cat)`：候选 URL 展开——分类目录优先、`pic/` 目录兜底，文件名按变体展开（原名优先，去 Django 去重后缀的原图兜底，`_name_variants`）
- `is_image_file(fname)`：按扩展名判断是否图片文件

---

### PortFakeWidget

`windows/port_fake.py`：虚假端口占用工具（工具菜单「端口占用」）——真实 `bind + listen` 模拟服务占用，`netstat -ano` 可见 `LISTENING`。

```python
PortFakeWidget(parent=None)
```

- `_occupy()`：占用输入框端口（保持监听）；支持多端口同时占用
- `_release_all()` / `_release_one(port)`：释放端口
- `closeEvent`：页面/窗口关闭时自动释放全部端口，避免占用残留
- 随机端口范围 20000~60000（`_RANDOM_MIN` / `_RANDOM_MAX`）

---

## main_window/ 主窗口层

### MainWindow

主窗口类，组合所有 Mixin：

```python
class MainWindow(SettingsMixin, ProcessMixin, RemoteMixin, UIMixin, FluentWindowBase):
    ...
```

#### 核心公开方法

| 方法 | 说明 |
|------|------|
| `on_flush_clicked()` | 刷新设备列表和程序列表 |
| `on_start_clicked()` | 启动 SnookerTracking 程序 |
| `on_end_clicked()` | 终止运行中的程序 |
| `on_start_three_clicked()` | 启动/关闭三端（识别端+后端+前端） |
| `on_open_daily_clicked()` | 打开 CPP 日志文件 |
| `on_open_dir_clicked()` | 打开当前设备目录 |
| `on_open_config_clicked()` | 打开配置文件对话框 |
| `apply_dpi_scale(settings_path)` | [静态] 应用 DPI 缩放 |
| `_effective_is_dark(settings)` | [静态] 判断是否深色主题 |
| `_show_info_bar(message, message_type="info", title=None, duration=2500)` | 统一 InfoBar 提示（兼容入口，内部转调 `core.utils.show_info_bar`，位置 BOTTOM_RIGHT、标题自动映射） |

---

### SettingsMixin

配置管理（settings.json 读写、路径加载、快捷键）。

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

---

### ProcessMixin

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

### RemoteMixin

远程连接管理（P2P 面板、XTCP/TCP 双模式、frpc 管理、窗口启动）。

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

---

### UiMixin

UI 辅助（状态栏、菜单栏、右键菜单、设置对话框、主题切换、布局切换）。

| 方法 | 说明 |
|------|------|
| `_init_statusbar()` | 初始化底部状态栏 |
| `_init_context_menus()` | 初始化右键菜单 |
| `_init_menubar()` | 初始化菜单栏（含「工具」菜单：单杆视频 + 端口占用 + 视频/日志批量整理） |
| `_apply_theme()` | 应用深色/浅色主题 |
| `_parse_theme_color(settings)` | [静态] 解析主题强调色：优先 `theme_color`（HEX），兼容旧 `highlight_color`（RGB 列表） |
| `_apply_theme_color()` | 从 settings.json 加载主题强调色到内存（`_apply_theme` 时应用） |
| `_on_theme_color()` | 弹出主题强调色选择对话框（功能菜单「主题颜色设置」），选色后经 `_apply_theme_color_set` 生效 |
| `_on_theme_color_reset()` | 还原默认主题强调色（功能菜单「还原默认主题色」），已是默认色时提示不重复执行 |
| `_apply_theme_color_set(color)` | 应用主题强调色：持久化 `theme_color` + `setThemeColor` 全局即时生效 + 日志/InfoBar |
| `_apply_font_size()` | 应用字号设置 |
| `_apply_font_family()` | 应用字体设置 |
| `_apply_layout()` | 应用布局模式（经典/默认） |
| `_on_open_single_video()` | 工具菜单「单杆视频」：校验 Worker 空闲 → 延迟导入探测 cv2/numpy → 打开 `SingleVideoDialog` 并注入 `_start` 回调（参数校验 → 保存 settings → 创建 `SingleVideoWorker` 连信号 → `exec()`） |
| `_on_open_port_fake()` | 工具菜单「端口占用」：弹窗真实监听指定端口模拟服务占用（`PortFakeWidget`） |
| `_on_newlog_organize()` | 工具菜单「视频/日志批量整理」：按 Excel 署名筛选批量归类（`NewLogDialog` + `NewLogWorker`），支持一键打包上传 |

---

### SettingsDialog

设置对话框（`main_window/settings_dialog.py`），继承 `MessageBoxBase`，Pivot 导航 + 分区懒加载（首次切入才构建控件）。

**分区**：路径配置 / 远程连接 / 收集与上传 / FRPC 服务器 / API Key / 日志高亮 / 外观。

**数据驱动 collect 机制**：`_CONFIG_ITEMS` 配置表描述每项 `(配置key, 所属分区, 控件获取lambda, 读取函数, 回退函数)`，`collect()` 循环统一收集——未构建分区的项用回退函数从原始配置取值，已构建的从控件读值。

**日志高亮规则**：`log_highlight_rules` 列表 `[{name, pattern, color, notify}]`，默认规则「错误」（红，通知）/「警告」（橙，静默）/「返回」「加分」「add」（旧版硬编码关键词迁移，橙，静默）；主窗口日志区实时匹配着色，`notify=True` 命中弹 InfoBar（每规则 10s 静默期）。

**NewLog 路径默认值**：`newlog_excel_dir` 默认 `~/Desktop/excel`、`newlog_out_dir` 默认 `~/Desktop`。

---

## win_api/ Windows API 层

### win_api.windows_api

Windows DLL 函数 ctypes 声明（仅 Windows 平台有效）。

#### 进程管理

| 函数 | 说明 |
|------|------|
| `win_suspend_process(pid)` | 挂起进程所有线程 |
| `win_resume_process(pid)` | 恢复进程所有线程 |

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

## p2p.py P2P 工具模块

| 函数 | 签名 | 说明 |
|------|------|------|
| `generate_random_port(exclude_ports=None)` | `(set?) -> int` | 生成随机端口（排除常用+已用端口） |
| `is_port_in_use(port, host='127.0.0.1')` | `(int, str) -> bool` | 检测端口是否被占用 |
| `open_xshell_and_xftp(host, port, ...)` | 使用 Xshell/Xftp 双开连接 | 外部工具调用 |

---

## 配置文件 settings.json

运行时配置文件，位于 exe 同目录。

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `exe_dir` | str | — | SnookerTracking 程序目录 |
| `videos_dir` | str | — | 视频/日志文件目录 |
| `cipher_tool` | str | — | AES 解码工具路径 |
| `front_exe` | str | — | 前端程序路径 |
| `backend_exe` | str | — | 后端程序路径 |
| `newlog_excel_dir` | str | `~/Desktop/excel` | NewLog 批量整理 Excel 目录 |
| `newlog_out_dir` | str | `~/Desktop` | NewLog 批量整理输出目录 |
| `last_exe` | str | — | 上次选择的程序名 |
| `dpi_scale` | int | 100 | DPI 缩放百分比 |
| `font_size` | int | 10 | 全局字号 |
| `font_family` | str | `Microsoft YaHei UI` | 全局字体 |
| `dark_theme` | bool | false | 深色主题开关（旧字段） |
| `theme_mode` | str | "auto" | 主题模式：auto/light/dark |
| `classic_layout` | bool | false | 经典布局模式 |
| `theme_color` | str | "#00BCD4" | 主题强调色（功能菜单「主题颜色设置」修改、「还原默认主题色」恢复，即时生效） |
| `highlight_color` | [r,g,b] | [220,80,20] | 日志高亮颜色（旧字段，已废弃，仅作 theme_color 兼容回退） |
| `log_highlight_rules` | [object] | 见默认 | 日志高亮规则列表 `[{name, pattern, color, notify}]` |
| `ssh_user` | str | — | SSH 默认用户名 |
| `ssh_pass` | str | — | SSH 默认密码（DPAPI 加密） |
| `tcp_servers` | [str] | [] | 保存的 TCP 服务器列表（ip:port） |
| `sftp_default_remote_path` | str | — | SFTP 默认远程路径 |
| `frpc_server` | object | — | frp 服务器配置（serverAddr/serverPort/auth_method/auth_token） |
| `upload_host` / `upload_port` | str/int | `49.235.34.253` / 22 | 上传 SFTP 服务器地址与端口 |
| `upload_remote_dir` | str | `/lhcos-data/videos` | 上传远端目录 |
| `upload_user` / `upload_pass` | str | `root` / — | 上传专用账号密码（DPAPI 加密，不复用 SSH 凭据） |
| `ai_vendor` | str | "deepseek" | AI 厂商标识（deepseek/qwen/kimi/zhipu/openai/gemini） |
| `ai_model` | str | — | AI 模型名（空则用厂商默认） |
| `ai_api_keys` | object | — | 各厂商 API Key 字典（DPAPI 加密） |
| `forensic_ai_analysis` | bool | true | 取证报告 AI 分析开关 |
| `remote_sessions` | [object] | [] | 保存的远程会话列表 |
| `perf_acrylic` | bool | true | 亚克力效果性能开关 |
| `perf_animation` | bool | true | 界面动画性能开关 |
| `api_credentials` | object | — | 运维面板 API 配置（见下表） |
| `mysql_sync` | object | — | MySQL 同步配置（见下表，售后面板/运维面板共用） |
| `aftersale_cycle` | object | `{type:tue}` | 售后统计周期模式（见下表） |
| `newlog_target_name` | str | — | NewLog 整理署名，同时作为售后面板填写人默认值 |
| `shortcut_*` | str | 见上表 | 快捷键配置（共 9 项） |

**api_credentials 子结构**（由管理设置页维护）：

| 字段 | 说明 |
|------|------|
| `active_source` | 启用的设备数据源：`kd`（默认）/ `xqzg` |
| `api1.username` / `api1.password` | 接口1 xqzg（Session 认证）账号密码 |
| `api2.username` / `api2.password` | 接口2 kd（JWT 认证）账号密码 |

**mysql_sync 子结构**（由运维面板/售后面板的 MySQL 同步卡片维护，密码 DPAPI 加密）：

| 字段 | 说明 |
|------|------|
| `enabled` | 是否启用（开启后 MySQL 完全替代本地 SQLite） |
| `host` / `port` | MySQL 服务器地址与端口（默认 3306） |
| `user` / `password` | 账号密码（密码 DPAPI 加密） |
| `database` | 数据库名（默认 autowork） |
| `auto_sync` | 每次 API 同步后自动推送到 MySQL |

**aftersale_cycle 子结构**（由售后面板「设置 → 统计周期设置」维护）：

| 字段 | 说明 |
|------|------|
| `type` | 周期模式：`tue`（周二起，默认）/ `mon`（自然周）/ `custom`（自定义） |
| `start` | custom 模式的周期起始日（`yyyy-MM-dd`） |
| `span` | custom 模式的周期天数（≥1，默认 7） |

---

## 依赖方向

```
core ← win_api ← workers ← windows ← main_window ← main.py
                    ↑          ↑
                    database ──┘
```

严格单向依赖，禁止循环导入。`database/` 仅被 `windows/management_panel.py` 等上层模块引用。
