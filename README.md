# AutoWork

基于 PySide6 开发的桌面自动化工具，用于视频播放控制、日志管理、数据记录及远程文件传输。

## 功能特性

- **视频帧控制**：支持"帧前"/"帧数"模式，精确控制视频播放帧位置
- **日志管理**：实时日志显示、本地日志文件查看、按日期筛选
- **错误提交**：支持错误类型/对象/描述/复现标记/新程序标记等信息提交
- **数据导出**：支持将记录数据导出为表格
- **远程连接（P2P）**：基于 frp XTCP 的 P2P 远程连接，支持 SSH 终端和 SFTP 文件管理
- **SFTP 文件管理**：双面板文件浏览器，支持上传/下载/删除/创建目录，带传输进度和队列
- **SSH 终端**：支持远程命令执行，ANSI 终端渲染
- **快捷键**：支持自定义快捷键绑定
- **主题切换**：支持深色/浅色主题切换

## 技术栈

- **Python 3** + **PySide6** (Qt6)
- **paramiko**：SSH/SFTP 远程连接
- **frp (frpc)**：P2P 内网穿透
- **PyInstaller**：打包为 Windows 可执行文件

## 项目结构

```
autowork/
├── main.py                    # 主程序入口（薄启动器）
├── autowork_with_table.py     # UI 定义（由 .ui 编译生成，勿手动修改）
├── autowork_with_table.ui     # Qt Designer 界面文件
├── p2p.py                     # P2P 连接模块（端口生成/检测/TOML 配置）
├── settings.json              # 运行时配置文件
├── frpc_xtcp.toml             # frp XTCP 连接配置
├── requirements.txt           # Python 依赖
├── AutoWork.spec              # PyInstaller 打包配置
├── build_exe.py               # 打包构建脚本
│
├── core/                      # 基础层（路径、日志、工具函数）
│   ├── app_paths.py           #   应用路径解析（兼容 PyInstaller）
│   ├── conn_logger.py         #   连接日志记录器 + Qt 消息处理器
│   └── utils.py               #   错误分类、自然排序、重试常量
│
├── win_api/                   # Windows API 层（ctypes 声明）
│   └── windows_api.py         #   显示设置/窗口嵌入/进程挂起恢复
│
├── workers/                   # 后台线程 Worker 层
│   └── network_workers.py     #   TCP/SFTP/SSH QThread Worker 类
│
├── windows/                   # 独立窗口层
│   ├── sftp_window.py         #   SFTP 双面板文件管理窗口
│   ├── ssh_terminal.py        #   SSH 终端窗口（ANSI 渲染）
│   └── rdp_window.py          #   RDP 远程桌面嵌入窗口
│
├── main_window/               # 主窗口层（Mixin 拆分）
│   ├── main_window.py         #   MainWindow 主类（组合所有 Mixin）
│   ├── settings_mixin.py      #   配置读写、快捷键
│   ├── process_mixin.py       #   三端进程管理（启动/关闭/暂停）
│   ├── remote_mixin.py        #   远程连接（frpc/SSH/SFTP/RDP）
│   └── ui_mixin.py            #   状态栏/右键菜单/设置对话框/主题
│
├── styles/                    # QSS 主题样式
│   ├── dark.qss               #   深色主题
│   └── light.qss              #   浅色主题
│
├── videos/                    # 视频文件目录
├── database/                  # 数据库目录
├── logs/                      # 日志目录
├── build/                     # 构建临时输出
└── dist/                      # 最终分发目录
```

### 依赖方向（单向，禁止循环导入）

```
core ← win_api ← workers ← windows ← main_window ← main.py
```

## 安装与运行

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

打包完成后，分发 `dist/AutoWork` 整个目录到目标机器。

## 注意事项

- `autowork_with_table.py` 由 Qt Designer 的 `.ui` 文件编译生成，修改界面请在 `.ui` 文件中操作后重新编译
- `settings.json` 用于存储运行时配置（如解码工具路径、DPI 缩放等）
- P2P 功能需要 `frpc.exe` 与主程序在同一目录下
- 主题样式文件位于 `styles/` 目录，打包时通过 `AutoWork.spec` 中的 `datas` 配置包含
- 新增模块请遵循单向依赖链，避免循环导入

## 许可证

石睿轩创作，由沈喆修改第二版。
