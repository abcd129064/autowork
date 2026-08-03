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
| `RETRYABLE_KEYWORDS` | `tuple` | 可重试错误关键词 |
| `RETRY_MAX` | `int = 5` | 最大重试次数 |
| `RETRY_DELAY` | `int = 2` | 重试间隔（秒） |

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

异步执行图像分类迁移（`POST /api/devices/migrate_image/`，form 参数 `src_path`/`dest_path`/`file_name`），支持批量，Token 过期自动重试。

```python
MigrateImageWorker(file_path, device_code, file_names,
                   src_category, dest_category,
                   username=None, password=None)
```

- `file_path`：日期路径，如 `"2026/08/02"`
- `src_category` / `dest_category`：中文分类名（见 `CATEGORY_DIRS`）

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

---

## database/ 数据层

### database.table_db

SQLite3 本地数据层（`database/tables.db`），线程内共享连接。

#### 球桌数据（wechat2-billiard）

| 函数 | 说明 |
|------|------|
| `save_all(rows)` | 全量覆盖写入球桌表，返回条数 |
| `query_page(page_no, page_size, keyword="")` | 分页查询，返回 `(total, rows)` |
| `insert_one(record)` | 手动插入一条记录 |
| `get_meta()` | 获取同步元信息（条数、时间） |

#### 设备状态数据（xqzg / kd）

| 函数 | 说明 |
|------|------|
| `save_xqzg(rows)` / `query_xqzg_page(page_no, page_size, keyword="")` | xqzg 数据存取（无日期分区，数据在 `results` 键） |
| `save_kd(rows, file_path="")` / `query_kd_page(page_no, page_size, keyword="", file_path="")` | kd 数据按日期分区存取（自动序列化/反序列化文件列表字段） |
| `get_kd_dates()` | 获取本地已有的 kd 日期分区列表 |

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

### ManagementPanelWindow

运维管理面板（`windows/management_panel.py`），qfluentwidgets `FluentWindow` + 左侧导航，三个功能页面。由主窗口「球桌管理」按钮打开（`UiMixin._on_open_table_panel`），支持 `python -m windows.management_panel` 独立调试。

```python
ManagementPanelWindow(parent=None)
```

**页面构成**：

| 页面 | 类 | 说明 |
|------|------|------|
| 球桌管理 | `TablePage` | wechat2-billiard 球桌数据：表格/搜索/分页/列筛选/右键复制/手动添加记录 |
| 设备状态 | `DevicePage` | kd / xqzg 数据源切换（`get_active_api_source()`），按日期分区查看；集成图片迁移 |
| 管理设置 | `AdminSettingsPage` | 数据源选择（kd/xqzg）、双接口账号密码、测试连接，合并写入 `settings.json` |

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

## main_window/ 主窗口层

### MainWindow

主窗口类，组合所有 Mixin：

```python
class MainWindow(SettingsMixin, ProcessMixin, RemoteMixin, UiMixin, FluentWindowBase):
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

UI 辅助（状态栏、右键菜单、设置对话框、主题切换、布局切换）。

| 方法 | 说明 |
|------|------|
| `_init_statusbar()` | 初始化底部状态栏 |
| `_init_context_menus()` | 初始化右键菜单 |
| `_init_menubar()` | 初始化菜单栏 |
| `_apply_theme()` | 应用深色/浅色主题 |
| `_apply_highlight_color()` | 应用日志高亮颜色 |
| `_apply_font_size()` | 应用字号设置 |
| `_apply_font_family()` | 应用字体设置 |
| `_apply_layout()` | 应用布局模式（经典/默认） |

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
| `last_exe` | str | — | 上次选择的程序名 |
| `dpi_scale` | int | 100 | DPI 缩放百分比 |
| `font_size` | int | 10 | 全局字号 |
| `font_family` | str | — | 全局字体 |
| `dark_theme` | bool | false | 深色主题开关（旧字段） |
| `theme_mode` | str | "auto" | 主题模式：auto/light/dark |
| `classic_layout` | bool | false | 经典布局模式 |
| `highlight_color` | [r,g,b] | [220,80,20] | 日志高亮颜色 |
| `ssh_user` | str | — | SSH 默认用户名 |
| `ssh_pass` | str | — | SSH 默认密码 |
| `tcp_servers` | [str] | [] | 保存的 TCP 服务器列表（ip:port） |
| `frpc_server` | object | — | frp 服务器配置 |
| `sftp_default_remote_path` | str | — | SFTP 默认远程路径 |
| `remote_sessions` | [object] | [] | 保存的远程会话列表 |
| `perf_acrylic` | bool | true | 亚克力效果性能开关 |
| `perf_animation` | bool | true | 界面动画性能开关 |
| `api_credentials` | object | — | 运维面板 API 配置（见下表） |
| `shortcut_*` | str | 见上表 | 快捷键配置（共 9 项） |

**api_credentials 子结构**（由管理设置页维护）：

| 字段 | 说明 |
|------|------|
| `active_source` | 启用的设备数据源：`kd`（默认）/ `xqzg` |
| `api1.username` / `api1.password` | 接口1 xqzg（Session 认证）账号密码 |
| `api2.username` / `api2.password` | 接口2 kd（JWT 认证）账号密码 |

---

## 依赖方向

```
core ← win_api ← workers ← windows ← main_window ← main.py
                    ↑          ↑
                    database ──┘
```

严格单向依赖，禁止循环导入。`database/` 仅被 `windows/management_panel.py` 等上层模块引用。
