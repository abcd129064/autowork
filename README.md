# AutoWork

基于 PySide6 + qfluentwidgets 开发的桌面自动化工具，用于台球追踪视频播放控制、日志管理、数据记录、运维管理及 P2P 远程文件传输。

## 功能特性

### 核心业务
- **视频帧控制**：支持"帧前"/"帧后"/"自定义"三种模式，精确控制视频播放起始帧
- **三端联动**：一键启动/关闭识别端 + 后端 + 前端（自动切换分辨率，关闭后恢复）
- **日志管理**：三列联动（设备 → 日志文件 → 日志内容），按日期筛选，关键词高亮
- **进程管理**：启动/终止/挂起/恢复 SnookerTracking 程序
- **detect.json 解码**：自动调用 AES 解码工具生成 detect.json

### 工具菜单（主界面菜单栏）
- **单杆视频**（收编 single_json 项目）：选择日志文件自动识别场次信息——`session_date` 从文件名自动解析（如 `20260810_230635.log` → `20260810`），`session_code` 自动生成（`日期_21位随机串`，与球桌接口 code 同格式）；后台 Worker 生成带计分水印的单杆视频
- **视频日志批量整理**：从「功能」菜单迁入工具菜单

### 运维管理面板（球桌管理按钮打开）
- **球桌管理**：对接 wechat2-billiard 接口，表格/搜索/分页/列筛选/右键复制/手动添加记录；接口 `code` 字段（设备编码）同步入库，界面默认隐藏，可在「筛选」菜单勾选显示
- **设备状态**：对接 kd / xqzg 双接口，数据源可切换，按日期分区查看设备状态
- **图片迁移**：点击总数/正常/操作单元格右侧滑出文件列表，点击文件选择目标分类（问题/精度/使用/废弃）即可在服务器上移动图片
- **管理设置**：配置双接口 API 账号密码、选择启用数据源、测试连接

### 远程连接（P2P）
- **XTCP 模式**：基于 frp 的 P2P 内网穿透，支持多 visitor 管理
- **TCP 模式**：直连服务器，保存服务器列表
- **SFTP 文件管理**：双面板文件浏览器，上传/下载/删除/重命名/创建，整目录递归传输，传输队列（暂停/恢复/取消）
- **SSH 终端**：交互式 PTY + ANSI 彩色渲染，Tab 补全，命令历史，Windows Terminal 风格
- **RDP 远程桌面**：嵌入系统 mstsc.exe 窗口，持续看门狗自动重连

### 界面与交互
- **Fluent Design**：基于 qfluentwidgets 的现代化 UI
- **深色/浅色/跟随系统**三种主题模式
- **亚克力磨砂效果**：打包后通过 PIL 补丁替代 numpy/scipy 实现
- **低性能模式**：设置中一键关闭亚克力/动画，低配机器更流畅（`perf_acrylic` / `perf_animation` 运行时即时生效）
- **统一提示条**：所有 InfoBar 提示统一走 `core.utils.show_info_bar()`（右下角、标题按类型自动映射），各模块不再各自直调
- **自定义快捷键**：9 个可配置快捷键
- **设备搜索**：Ctrl+F 实时过滤设备列表
- **双布局模式**：默认/经典布局一键切换

## 技术栈

| 组件 | 技术 |
|------|------|
| GUI 框架 | PySide6 (Qt6) + PySide6-Fluent-Widgets |
| HTTP/API | requests（wechat2-billiard / xqzg / kd 三接口） |
| 本地数据库 | SQLite3（球桌/设备状态缓存） |
| SSH/SFTP | paramiko |
| P2P 穿透 | frp (frpc) XTCP |
| 远程桌面 | mstsc.exe + Win32 API 窗口嵌入 |
| 打包 | PyInstaller (onedir) |
| 进程管理 | QProcess + ctypes (NtSuspendProcess) |
| 显示控制 | ctypes (EnumDisplaySettings/ChangeDisplaySettings) |

## 项目结构

```
autowork/
├── main.py                    # 主程序入口（薄启动器）
├── autowork_with_table.py     # UI 定义（由 .ui 编译生成，勿手动修改）
├── autowork_with_table.ui     # Qt Designer 界面文件
├── p2p.py                     # P2P 工具（端口生成/检测）
├── settings.json              # 运行时配置文件
├── frpc.exe                   # frp 客户端（P2P 穿透）
├── frpc_xtcp.toml             # frp XTCP 连接配置（运行时生成）
├── requirements.txt           # Python 依赖
├── AutoWork.spec              # PyInstaller 打包配置
├── build_exe.py               # 打包构建脚本
│
├── core/                      # 基础层（路径、日志、工具函数）
│   ├── acrylic_patch.py       #   亚克力效果 PIL 替代补丁
│   ├── app_paths.py           #   应用路径解析（兼容 PyInstaller）
│   ├── conn_logger.py         #   连接日志记录器 + Qt 消息处理器
│   ├── frp_remote.py          #   frpc 管理、统一远程会话中心（RemoteSessionManager）
│   ├── perf.py                #   低性能模式开关（亚克力/动画运行时控制）
│   ├── secrets.py             #   配置加解密（upload/api 凭据）
│   └── utils.py               #   错误分类、自然排序、统一提示 show_info_bar
│
├── win_api/                   # Windows API 层（ctypes 声明）
│   └── windows_api.py         #   显示设置/窗口嵌入/进程挂起恢复
│
├── workers/                   # 后台线程 Worker 层
│   ├── network_workers.py     #   TCP/SFTP/SSH QThread Worker 类
│   ├── single_video_worker.py #   单杆视频生成 Worker（日志解析 + 计分水印）
│   └── table_worker.py        #   球桌/设备数据 API Worker（拉取/迁移/登录测试）
│
├── database/                  # 本地数据层
│   ├── table_db.py            #   SQLite 存取（球桌/xqzg/kd 按日期分区）
│   └── tables.db              #   SQLite 数据库文件
│
├── windows/                   # 独立窗口层
│   ├── management_panel.py    #   运维管理面板（球桌管理/设备状态/管理设置）
│   ├── single_video_dialog.py #   单杆视频参数对话框（工具菜单）
│   ├── table_panel.py         #   球桌面板（旧版，仅 AddRecordDialog 仍被引用）
│   ├── sftp_window.py         #   SFTP 双面板文件管理窗口
│   ├── ssh_terminal.py        #   SSH 终端窗口（ANSI 渲染）
│   ├── ansi_terminal.py       #   ANSI 虚拟终端控件
│   ├── rdp_window.py          #   RDP 远程桌面嵌入窗口
│   ├── remote_session_window.py # 远程会话窗口（frpc 日志/隧道状态）
│   ├── tunnel_panel.py        #   当前隧道面板
│   ├── conn_diag_panel.py     #   连接诊断面板
│   └── moyu_widgets.py        #   摸鱼阅读器（内置小游戏/网页抓取）
│
├── main_window/               # 主窗口层（Mixin 拆分）
│   ├── main_window.py         #   MainWindow 主类（组合所有 Mixin）
│   ├── settings_mixin.py      #   配置读写、快捷键
│   ├── process_mixin.py       #   三端进程管理（启动/关闭/暂停/分辨率）
│   ├── remote_mixin.py        #   远程连接（frpc/SSH/SFTP/RDP）
│   └── ui_mixin.py            #   状态栏/菜单栏（含工具菜单）/右键菜单/设置对话框/主题
│
├── styles/                    # QSS 主题样式
│   ├── dark.qss               #   深色主题
│   └── light.qss              #   浅色主题
│
├── docs/                      # 文档
│   └── API.md                 #   接口文档
│
├── videos/                    # 视频/日志文件目录
├── logs/                      # 运行日志目录
├── build/                     # 构建临时输出
└── dist/                      # 最终分发目录
```

### 依赖方向（单向，禁止循环导入）

```
core ← win_api ← workers ← windows ← main_window ← main.py
                    ↑          ↑
                    database ──┘
```

## 快速开始

### 环境要求

- Python 3.10+
- Windows 10/11（RDP 嵌入、进程挂起等功能依赖 Win32 API）

### 安装依赖

```bash
pip install -r requirements.txt
```

### 开发模式运行

```bash
python main.py
```

### 打包为 exe

```bash
python build_exe.py
```

打包完成后，分发 `dist/AutoWork/` 整个目录到目标机器即可运行。

> 构建脚本会自动将 `settings.json` 和 `frpc.exe` 复制到 dist 目录。

## 快捷键

| 功能 | 默认键 | 说明 |
|------|--------|------|
| 刷新数据 | `F5` | 重新扫描程序和设备列表 |
| 播放/结束 | `Space` | 切换启动/终止程序 |
| 打开目录 | `Ctrl+O` | 打开当前设备目录 |
| 暂停/恢复 | `P` | 挂起/恢复运行中进程 |
| 聚焦帧数框 | `Ctrl+G` | 聚焦并全选帧数输入框 |
| 启动三端 | `Ctrl+T` | 启动/关闭三端程序 |
| 查看CPP日志 | `Ctrl+L` | 打开 daily 日志文件 |
| 打开配置 | `Ctrl+,` | 打开配置文件选择对话框 |
| P2P面板 | `F9` | 切换远程面板显隐 |
| 设备搜索 | `Ctrl+F` | 切换设备搜索框 |

所有快捷键均可在设置对话框中自定义修改。

## 配置说明

配置文件 `settings.json` 位于 exe 同目录，首次运行自动生成。详见 [接口文档 - 配置文件](docs/API.md#配置文件-settingsjson)。

运维管理面板相关的 API 配置存于 `api_credentials` 节点：

```json
"api_credentials": {
  "active_source": "kd",
  "api1": { "username": "...", "password": "..." },
  "api2": { "username": "...", "password": "..." }
}
```

- `active_source`：设备状态页启用的数据源（`kd` / `xqzg`）
- `api1`：xqzg.newbv.cn 接口（Session 认证）
- `api2`：kd.newbv.cn:30005 接口（JWT 认证，支持图片迁移）

也可直接在「运维管理面板 → 管理设置」页中修改并测试连接。

低性能模式（设置 → 性能）：

```json
"perf_acrylic": false,
"perf_animation": false
```

- `perf_acrylic`：亚克力磨砂效果开关
- `perf_animation`：界面动画开关

均为运行时即时生效，无需重启，低配机器可全部关闭提升流畅度。

## 接口文档

完整的模块接口说明请参阅 [docs/API.md](docs/API.md)。

## 注意事项

- `autowork_with_table.py` 由 `.ui` 文件编译生成，修改界面请编辑 `.ui` 后重新编译
- P2P 功能需要 `frpc.exe` 与主程序在同一目录下
- 打包排除了 numpy/scipy（避免 MKL DLL 245MB），亚克力效果由 PIL 补丁替代实现
- 主题样式文件位于 `styles/`，打包时通过 `AutoWork.spec` 的 `datas` 包含
- 新增模块请遵循单向依赖链，避免循环导入
- 连接日志自动落盘到 `logs/autowork_conn.log`（2MB 轮转）

## 许可证

石睿轩创作，由沈喆修改第二版。
