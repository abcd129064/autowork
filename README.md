# AutoWork

基于 PySide6 + qfluentwidgets 开发的台球追踪业务工具集，用于台球追踪视频播放控制、日志管理、数据记录、运维管理、售后登记与统计及 P2P 远程文件传输；配套**售后面板 Web 端**（FastAPI 后端 + Vue 前端 v1/v2 双版本并行部署），与桌面端共用同一 MySQL 数据源。

桌面主窗口为 FluentWindow 单窗口结构（2026-09 重构）：左侧导航 **工作台 / 运维管理 / 售后 / 跑视频 / 远程 / 工具**，底部 **设置 / 关于**。工作台为四列并列布局（会话列表 / 设备 / 日志文件 / 日志输出，列间可拖动）；运维/售后/跑视频/远程/工具均为「Pivot 二级导航 + 工作区」容器页，原独立面板窗口降层嵌入主窗口。

## 功能特性

### 核心业务
- **视频帧控制**：支持"帧前"/"帧后"/"自定义"三种模式，精确控制视频播放起始帧
- **三端联动**：一键启动/关闭识别端 + 后端 + 前端（自动切换分辨率，关闭后恢复）
- **日志管理**：三列联动（设备 → 日志文件 → 日志内容），按日期筛选，关键词高亮
- **进程管理**：启动/终止/挂起/恢复 SnookerTracking 程序
- **detect.json 解码**：自动调用 AES 解码工具生成 detect.json

### 工具页（左侧导航「工具」，Pivot 四工作区）
- **单杆视频**（收编 single_json 项目）：选择日志文件自动识别场次信息——`session_date` 从文件名自动解析（如 `20260810_230635.log` → `20260810`），`session_code` 自动生成（`日期_21位随机串`，与球桌接口 code 同格式）；参数卡右侧带**日志预览**面板（大小·行数/悬停完整路径/256KB 截断提示/随选择联动），参数区与「运行输出」终端间可上下拖动分隔；后台 Worker 生成带计分水印的单杆视频（PIL 在比分条模板上画文字/头像按 alpha 叠帧；选手0 用原图，选手1 用镜像图且品牌图形还原防翻反）
- **上传清单**：复选框表格勾选待上传文件（表头浮全选框），打包 zip 后经 SFTP 上传到服务器，仅清理已传文件；「删除所选」带二次确认与路径越界防护
- **视频/日志批量整理**：按 Excel 署名筛选，批量归类视频/日志/配置文件（NewLog），完成后可一键打包上传
- **端口占用**：真实监听指定端口模拟服务占用（TCP `netstat -ano` 可见 LISTENING / UDP bind），用于测试端口冲突/验证服务检测逻辑

> 菜单栏「工具」及各入口已统一改为跳转工具页对应工作区；工具页/远程页另可通过 `HubPopoutWindow` 弹出为独立窗口使用。

### 运维管理页（左侧导航「运维管理」，Pivot 多工作区；原运维面板降层嵌入）
- **球桌管理**：对接 wechat2-billiard 接口，表格/搜索/分页/列筛选/右键复制/手动添加记录；接口 `code` 字段（设备编码）同步入库，界面默认隐藏，可在「筛选」菜单勾选显示
- **设备状态**：对接 kd / xqzg 双接口，数据源可切换，按日期分区查看设备状态；总数/正常/操作列点击可查看文件清单，双击/右键预览图片（左右键翻页，支持分类迁移）
- **设备健康度管理**：基于接口 `health` 字段的健康度异常告警（每 30 分钟自动拉取，阈值 4000/5000/40 万），支持标记已处理、一键归零（逐台把服务端健康度重置为 4000），异常恢复后自动重新告警
- **图片迁移**：点击总数/正常/操作单元格右侧滑出文件列表，点击文件选择目标分类（问题/精度/使用/废弃）即可在服务器上移动图片
- **控件测试**：FluentIcon 图标库（搜索过滤、点击复制枚举名）+ qfluentwidgets 控件墙（按钮/输入/日期/弹窗分组演示，可直接交互）
- **管理设置**：配置双接口 API 账号密码、选择启用数据源、测试连接
- **小游戏**：摸鱼中心（小说阅读 / 2048 / 贪吃蛇 / 扫雷），贪吃蛇与扫雷带难度选择，成绩按难度分档本地留存

### 跑视频页（左侧导航「跑视频」；原「跑视频面板」按钮降层嵌入）
- **会话预填**：打开面板自动带入当前球桌会话（球房/视频名/帧数/署名），未选设备也可打开（球房预填空，面板内手填），表单确认后提交
- **字段对齐在线模板**：分类（问题/未复现/精度/使用）→ 类别 → 球房 → 视频名 → 帧数 → 日期 → 描述/备注 → 复现 → 新程序 → 署名，类别候选预置模板历史取值 + 自由输入；复现/新程序为是/否开关，日期为视频日期（默认当天可翻历史）
- **填写录入**：必填进度（分类/类别/球房）+ 分类联动类别候选，提交即入库
- **记录与统计**：四分类指标卡 + 日期/分类/类别/署名/复现筛选（按天统计：全部/今天/一周/一月/自定义）+ 分页 + 编辑/删除 + 按署名统计（模板「计数」sheet 的电子版）+ 按分类分 sheet 导出 xlsx（结构与在线模板一致，仅作线下汇报用）
- **多后端存储**：与售后同一条双后端路由——MySQL 开启时读写直接落服务器，多人协作刷新可见；MySQL 不可用自动降级本地 SQLite，恢复后自动合并回 MySQL
- **设置**：默认署名配置 + 数据库设置（复用 MysqlSyncCard，同步范围为跑视频记录）

### 数据保留自动清理（防数据无限增长）
- **过期清理（每日执行）**：`xqzg_status` / `kd_status` 两表按日期分区（`file_path`=`yyyy/MM/dd`）自动删除超过 60 天的数据
- **按大小清理（每 60 天检查）**：`aftersale_records` / `ledger_records` / `submission_log` / `health_alerts` 四张流水表，表大小超过 3GB 时从最早日期开始逐日删除，直到小于 2GB；最近 30 天数据受保护不删
- **双后端生效**：MySQL 主库与本地 SQLite 兜底均执行同一套清理；参数在 `config/database.json` 的 `data_retention` 键配置（见下文）
- 清理在后台线程执行（启动后延迟 8s 首次检查 + 每 24h 一次），仅在确有删除时提示

### 售后页（左侧导航「售后」；与 Web 端售后面板同库同口径）
- **填写录入**：售后问题登记表单（字段对齐售后汇总 Excel），球房输入搜索球桌库自动带出桌号/SNK/地区，发生时间步进补录历史日期；「是否我们发起售后」「是否我方问题」两个判定开关参与筛选与统计
- **记录与统计**：按周期/类型/状态/是否我们发起/是否我方问题/关键词筛选 + 分页 + 已解决/未解决统计，支持编辑/删除/批量标记已解决/批量删除/导出 xlsx/导入 Excel（导入前预览确认）；支持自动刷新（开关/间隔在设置-面板设置）
- **周期管理**：统计周期可配置（周二起默认/自然周/自定义起始日+天数/自然月），记录归属周期物化落库（查询走覆盖索引，10 万条记录周期筛选毫秒级），列表/统计/周期下拉/导出四处口径一致
- **统计图表**：记录页一键打开 pygwalker 自助分析窗口（默认预置图表可拖拽探索，数据源与面板筛选同口径）
- **多后端存储**：本地 SQLite / 远程 MySQL 双后端，跟随数据库设置开关切换；MySQL 模式下多人各自提交即落库，刷新可见
- **数据库设置**：MySQL 连接配置、测试连接；启用后直接读写远程库，不可用时自动降级本地 SQLite

### 远程页（左侧导航「远程」，Pivot 三视图）
- **会话总览**：统计卡 + 隧道表（行内一键 SSH / SFTP / RDP / 断开 / 删除；SFTP 传输中删除有二次确认）
- **P2P 访客**：XTCP visitor 注册管理（基于 frp 的 P2P 内网穿透；注册只持久化不自动拉起 frpc）
- **隧道配置**：frpc 服务器配置与进程控制 + 实时日志终端
- **TCP 模式**：直连服务器，保存服务器列表
- **SFTP 文件管理**：双面板文件浏览器，上传/下载/删除/重命名/创建，整目录递归传输，传输队列（暂停/恢复/取消）
- **SSH 终端**：交互式 PTY + ANSI 彩色渲染，Tab 补全，命令历史，Windows Terminal 风格
- **RDP 远程桌面**：嵌入系统 mstsc.exe 窗口，持续看门狗自动重连
- **SSH 故障取证**：连接失败时一键生成诊断取证包（球桌信息/设备状态/会话日志/连接日志 + 诊断命令输出），可选 AI 分析定位问题
- **连接诊断**：位于设置 → 工具页「连接诊断」行，独立弹窗（不占远程页视图）

### 售后面板 Web 端（v1/v2 并行，与桌面端同库）
浏览器访问的售后登记与统计系统，数据与桌面端售后页共库（MySQL `aftersale_records` 表，周期口径/筛选口径完全一致）。

- **后端**（`web/aftersale_api/app.py`，FastAPI + pymysql）：只读接口默认开放（记录分页+同口径统计、周期下拉、图表聚合、通用维度聚合）；写入接口（新增/编辑/删除/批量解决/批量删除）受 `WRITE_ENABLED` 环境开关控制，编辑带 `updated_at` 乐观锁（409 冲突）；认证（JWT + bcrypt）受 `AUTH_ENABLED` 控制
- **v1 前端**（`web/aftersale_front`，Vue3 + Vite 轻量版）：售后记录列表/筛选/统计图表/自定义图表
- **v2 前端**（`web/vue-pure-admin`，vue-pure-admin 7.0 全功能版）：售后列表 / 录入表单 / 统计页，demo 菜单保留、策略为「先不裁剪，只加售后页」
- **生产部署**：`http://49.235.34.253/` 入口选择页 → `/v1/` 老系统、`/v2/` 新系统并行；nginx 零改动（既有 `try_files $uri $uri/ /index.html` 直接服务子目录 SPA），前端产物根 `/opt/aftersale-web/dist`，后端 `aftersale-web.service`（uvicorn + `.env`）；部署脚本 `tools/deploy_parallel_v1v2.py`（v1/v2 并行整包）/ `tools/upload_aftersale_dist.py`（v1 产物）/ `tools/deploy_aftersale_api.py`（后端），SSH 统一走 `tools/prod_ssh.py`（密码只从 `AFT_SSH_PASS` 环境变量读取，不落盘）
- **本地预览**：桌面程序内置 `core.local_web_server`（默认 `http://localhost:8787`），托管 v1 构建产物 + 反代云端 API，与线上站点同一数据源；另可用 `web/aftersale_front/tools/serve_dist_v2.mjs` 本地仿真 v2 产物

### 界面与交互
- **Fluent Design**：基于 qfluentwidgets 的现代化 UI
- **深色/浅色/跟随系统**三种主题模式
- **亚克力磨砂效果**：打包后通过 PIL 补丁替代 numpy/scipy 实现
- **低性能模式**：设置中一键关闭亚克力/动画，低配机器更流畅（`perf_acrylic` / `perf_animation` 运行时即时生效）
- **日志高亮规则**：设置中可配置多条正则高亮规则（颜色 + 通知开关），日志区实时匹配着色，命中可弹窗提醒
- **统一提示条**：所有 InfoBar 提示统一走 `core.utils.show_info_bar()`（右下角、标题按类型自动映射），各模块不再各自直调
- **自定义快捷键**：12 个可配置快捷键
- **默认启动页面**：设置 → 应用配置「启动」组可指定打开程序时展示的界面（工作台/运维管理/售后/跑视频/远程/工具，下次启动生效）
- **设备搜索**：Ctrl+F 实时过滤设备列表
- **双布局模式**：默认/经典布局一键切换
- **自动版本号**：基于 git 提交数自动计算（`core/version.py`），标题栏展示 `主.次.提交数`

## 技术栈

| 组件 | 技术 |
|------|------|
| GUI 框架 | PySide6 (Qt6) + PySide6-Fluent-Widgets |
| HTTP/API | requests（wechat2-billiard / xqzg / kd 三接口） |
| 本地数据库 | SQLite3（球桌/设备状态/售后/跑视频记录缓存） |
| 远程数据库 | MySQL（pymysql，MySQL 主库 + SQLite 兜底，自动降级与合并回写） |
| Excel 读写 | openpyxl（售后记录导入/导出） |
| SSH/SFTP | paramiko |
| P2P 穿透 | frp (frpc) XTCP |
| 远程桌面 | mstsc.exe + Win32 API 窗口嵌入 |
| 视频渲染 | OpenCV + Pillow（单杆视频逐帧计分水印合成，PIL 绘制比分条） |
| Web 后端 | FastAPI + uvicorn + pymysql（售后面板 Web，`web/aftersale_api`） |
| Web 前端 | v1: Vue3 + Vite + axios/echarts；v2: vue-pure-admin 7.0（pnpm + Vite + TS） |
| Web 认证/存储 | JWT（pyjwt）+ bcrypt（AUTH_ENABLED 开启后），用户文件 users.json |
| 打包 | PyInstaller（完整版 onedir + 售后面板单文件 onefile） |
| 进程管理 | QProcess + ctypes (NtSuspendProcess) |
| 显示控制 | ctypes (EnumDisplaySettings/ChangeDisplaySettings) |

## 项目结构

```
autowork/
├── main.py                    # 主程序入口（薄启动器）
├── autowork_with_table.py     # 经典布局 UI 定义（由 .ui 编译生成，勿手动修改）
├── autowork_with_table.ui     # Qt Designer 界面文件
├── p2p.py                     # P2P 工具（端口生成/检测）
├── config/                    # 分域配置目录（原 settings.json，2026-09-06 拆分）
│   ├── aftersale.json         #   售后：周期模式/常用句/署名记忆
│   ├── perf.json              #   性能开关（亚克力/动画/表格平滑滚动）
│   ├── database.json          #   数据库：MySQL 连接/数据保留清理（密码 DPAPI 加密）
│   ├── credentials.json       #   凭据：SSH/上传/API/AI（DPAPI 加密）
│   ├── ui.json                #   界面：主题/字体/DPI/高亮规则
│   ├── paths.json             #   路径：程序目录/视频目录等
│   ├── remote.json            #   远程：会话列表/隧道密钥
│   └── misc.json              #   兜底：web_port/快捷键等未登记键
├── settings.json.bak          # 旧单文件配置（拆分迁移后自动改名，可回滚）
├── frpc.exe                   # frp 客户端（P2P 穿透）
├── frpc_xtcp.toml             # frp XTCP 连接配置（运行时生成）
├── requirements.txt           # Python 依赖
├── AutoWork.spec              # PyInstaller 打包配置（完整版 onedir）
├── AfterSale.spec             # PyInstaller 打包配置（售后面板单文件 onefile）
├── build_exe.py               # 打包构建脚本（完整版 + 单文件售后面板）
│
├── core/                      # 基础层（路径、日志、配置、性能、工具函数）
│   ├── acrylic_patch.py       #   亚克力效果 PIL 替代补丁
│   ├── ai_providers.py        #   AI 厂商注册表（六家 OpenAI 兼容接入）
│   ├── app_paths.py           #   应用路径解析（兼容 PyInstaller）
│   ├── app_settings.py        #   配置门面（config/ 分域读写，原 settings.json）
│   ├── conn_logger.py         #   连接日志记录器 + Qt 消息处理器
│   ├── design_tokens.py       #   设计令牌（语义色/间距/字号单一来源）
│   ├── flow_widgets.py        #   流式工具栏共享组件（FlowToolbarScrollArea）
│   ├── frp_remote.py          #   frpc 管理、统一远程会话中心（RemoteSessionManager）
│   ├── lean_table_delegate.py #   表格轻量委托（渲染性能）
│   ├── local_web_server.py    #   本地售后面板 Web 服务（静态页 + 云端 API 反代）
│   ├── log_rules.py           #   日志高亮规则引擎（原设置对话框迁入）
│   ├── perf.py                #   性能开关中心（亚克力/动画/表格平滑滚动 + 中央补丁）
│   ├── secrets.py             #   配置加解密（DPAPI，SSH/upload/AI 凭据）
│   ├── switch_cn_patch.py     #   SwitchButton 中文「开/关」文本补丁
│   ├── theme_qss.py           #   窗口级 QSS 应用（apply_window_qss/current_accent_hex）
│   ├── utils.py               #   错误分类、自然排序、统一提示 show_info_bar
│   └── version.py             #   版本号自动计算（主.次.git提交数）
│
├── win_api/                   # Windows API 层（ctypes 声明）
│   └── windows_api.py         #   显示设置/窗口嵌入/进程挂起恢复
│
├── workers/                   # 后台线程 Worker 层
│   ├── aftersale_worker.py    #   通用 DB 后台 Worker（售后/跑视频/运维共用，fn 封装）
│   ├── backup_worker.py       #   周备份 Worker（MySQL → SQLite 兜底基线刷新）
│   ├── cleanup_worker.py      #   数据保留清理 Worker（过期分区 + 按大小清理）
│   ├── collect_worker.py      #   视频/日志收集与打包上传 Worker
│   ├── merge_back_worker.py   #   兜底增量合并回写 Worker（MySQL 恢复后 LWW）
│   ├── mysql_sync_worker.py   #   MySQL 连接测试 Worker
│   ├── network_workers.py     #   TCP/SFTP/SSH QThread Worker 类
│   ├── newlog_worker.py       #   NewLog 批量整理 Worker（日志逐行转发 GUI）
│   ├── single_video_worker.py #   单杆视频生成 Worker（日志解析 + 计分水印）
│   └── table_worker.py        #   球桌/设备数据 API Worker（拉取/迁移/登录测试）
│
├── database/                  # 数据层（SQLite/MySQL 双后端）
│   ├── backend.py             #   数据库后端切换层（MySQL 替代 SQLite 路由 + 方言适配）
│   ├── table_db.py            #   球桌/设备数据存取（FTS5 搜索、按日期分区）
│   ├── data_retention.py      #   数据保留自动清理（过期分区 + 按大小清理，双后端）
│   ├── aftersale_db.py        #   售后记录数据层（周期计算、筛选统计、导入导出）
│   ├── ledger_db.py           #   跑视频记录数据层（分类筛选统计、署名统计、导出）
│   ├── schema.py              #   表结构单一来源（双方言 DDL + 迁移注册表）
│   ├── merge_back.py          #   兜底增量 LWW 合并回写（恢复后执行）
│   ├── fallback_backup.py     #   MySQL → SQLite 周备份（兜底基线刷新）
│   ├── sqlite_io.py           #   SQLite 只读工具（列交集/缺列补位）
│   ├── mysql_sync_card_logic.py # MysqlSyncCard 入口判定纯函数（无 GUI 依赖）
│   ├── mysql_sync.py          #   MySQL 连接测试工具（镜像推送已下线，仅保留测试连接）
│   └── tables.db              #   SQLite 数据库文件
│
├── windows/                   # 独立窗口与面板层（大面板按子包拆分 + re-export shim）
│   ├── management_panel.py    #   运维管理面板（re-export shim）
│   ├── management/            #   运维管理拆分包（球桌/设备/健康度/趋势/摸鱼等页面）
│   ├── aftersale_panel.py     #   售后面板（re-export shim）
│   ├── aftersale/             #   售后面板拆分包（表单/录入/记录/设置/弹窗/统计）
│   ├── ledger_panel.py        #   跑视频面板（re-export shim）
│   ├── run_video/             #   跑视频面板拆分包（表单/录入/记录/设置/窗口）
│   ├── remote_session/        #   远程会话窗口（SFTP/SSH/RDP/取证/隧道/诊断）
│   ├── mysql_sync_card.py     #   MySQL 连接配置卡片（运维/售后/跑视频面板共用）
│   ├── single_video_dialog.py #   单杆视频参数对话框/默认值常量
│   ├── stat_charts.py         #   统计图表自助分析（pygwalker）
│   └── tools/                 #   工具页（ToolHub）功能后端（运行时业务逻辑）
│       ├── single_shot_video.py #  单杆视频渲染（比分条模板 + PIL 水印合成）
│       ├── single_video_tool.py #  单杆 json 生成（日志解析/开球局提取）
│       ├── port_fake.py       #   端口占用模拟器（真实监听指定端口）
│       ├── newlog.py          #   视频/日志批量整理主流程（Excel 驱动，可 CLI 独立运行）
│       └── smoke_fluent_mainwindow.py  # 主窗口 GUI 冒烟脚本（offscreen）
│
├── main_window/               # 主窗口层（Mixin + Hub 页面）
│   ├── main_window.py         #   MainWindow 主类（组合 Mixin，注册导航 Hub）
│   ├── hub_pages.py           #   业务域容器页（ManagementHub/AftersaleHub/LedgerHub/SettingsHub/About）
│   ├── tool_hub.py            #   工具页（四工作区：单杆视频/上传清单/批量整理/端口占用）
│   ├── remote_hub.py          #   远程页（三视图：会话总览/P2P访客/隧道配置）
│   ├── pivot_page.py          #   Pivot 二级导航容器页基建
│   ├── hub_popout.py          #   Hub 弹出为独立窗口（HubPopoutWindow）
│   ├── setting_cards.py       #   统一设置页卡片组件（SettingGroup/SettingRow）
│   ├── settings_mixin.py      #   配置读写、快捷键
│   ├── process_mixin.py       #   三端进程管理（启动/关闭/暂停/分辨率）
│   ├── remote_mixin.py        #   远程连接（frpc/SSH/SFTP/RDP）
│   └── ui_mixin.py            #   状态栏/菜单栏/右键菜单/主题
│
├── tools/                     # 开发/运维辅助脚本（部署、探针、回归、压测，不参与运行时）
│   ├── prod_ssh.py            #   生产机 SSH 执行器（AFT_SSH_PASS 环境变量取密）
│   ├── deploy_parallel_v1v2.py#   Web v1/v2 并行部署脚本
│   ├── upload_aftersale_dist.py / deploy_aftersale_api.py  # 前端产物 / 后端部署
│   ├── shot_fluent_mainwindow.py  # GUI 真机截图
│   └── ...                    #   性能压测、探针、回归脚本等
│
├── web/                       # 售后面板 Web 端
│   ├── aftersale_api/app.py   #   FastAPI 后端（只读默认开放；写/认证受开关控制）
│   ├── aftersale_front/       #   v1 前端（Vue3 + Vite；tools/ 含部署验证脚本）
│   ├── vue-pure-admin/        #   v2 前端（vue-pure-admin 7.0，src/views/aftersale）
│   └── aftersale_chooser/     #   生产入口选择页（index.html → /v1/ /v2/）
│
├── styles/                    # QSS 主题样式
│   ├── dark.qss               #   深色主题
│   └── light.qss              #   浅色主题
│
├── docs/                      # 文档
│   ├── API.md                 #   接口文档（含 Web 端 API 契约）
│   ├── MySQL兜底降级设计.md    #   双后端降级/合并设计
│   ├── PERF_REVIEW.md         #   性能评审
│   └── ...                    #   调查报告、方案与审计文档
│
├── tests/                     # pytest 测试（基线 270 passed）
├── resource/                  # 随包资源（比分条模板/字体/头像）
├── design/                    # 界面设计稿（HTML/PNG）
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

打包完成后产出**两个相互独立的分发物**（互不关联，各自数据独立）：

1. **完整版**：分发 `dist/AutoWork/` 整个目录到目标机器运行（`autowork.exe`）。
   售后面板、运维面板均**内置**在该程序内（打开时不会调用任何外部 exe）。
2. **单文件售后面板**：分发 `dist/aftersale.exe`（单个 exe，自带全部依赖），
   无需安装环境即可运行。数据落盘在 exe 旁边的 `database/tables.db`，
   与完整版的数据相互独立。

> 构建脚本会自动复制 `config/` 分域配置目录到两个产物旁（旧版 `settings.json` 若存在则一并复制作为迁移源），并复制 `frpc.exe` 到完整版目录。
> 单文件版不包含 P2P/SSH/运维/AI 等主程序功能，仅售后面板。

### 售后面板 Web 端（本地开发）

```bash
# 后端（需 Python 3.10+ 与 MySQL 访问权限；凭据写 .env 或环境变量）
cd web/aftersale_api
pip install fastapi uvicorn pymysql python-jose bcrypt
uvicorn app:app --port 8800

# v1 前端（Vue3 + Vite）
cd web/aftersale_front && npm install && npm run dev

# v2 前端（vue-pure-admin，pnpm）
cd web/vue-pure-admin && pnpm install && pnpm dev
```

生产部署（v1/v2 并行到 49.235.34.253，详见接口文档「售后面板 Web API」章节）：

```bash
# 前端产物（v1 整包 / v1+v2 并行布局）
AFT_SSH_PASS='...' python tools/upload_aftersale_dist.py
AFT_SSH_PASS='...' python tools/deploy_parallel_v1v2.py
# 后端 app.py + 重启服务
AFT_SSH_PASS='...' python tools/deploy_aftersale_api.py
```

桌面程序运行时也会自动启动本地 Web 服务（`core.local_web_server`，默认 `http://localhost:8787`），托管 v1 构建产物并反代云端 API。


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
| 打开配置 | `Ctrl+,` | 打开 config/ 配置目录 |
| P2P面板 | `F9` | 切换远程面板显隐 |
| 设备搜索 | `Ctrl+F` | 切换设备搜索框 |
| 跑视频页 | `Ctrl+1` | 跳转导航「跑视频」（预填当前球桌会话） |
| 售后页 | `Ctrl+2` | 跳转导航「售后」（售后记录登记与统计） |
| 运维管理页 | `Ctrl+3` | 跳转导航「运维管理」（球桌/设备/健康/设置） |

所有快捷键均可在设置对话框中自定义修改。

## 配置说明

配置自 2026-09-06 起按域拆分到 `config/` 目录（原单文件 `settings.json` 自动迁移为 `settings.json.bak`，旧文件若重新出现会在应用首启自动分拣），读写统一经 `core/app_settings.py` 配置门面（键自动路由 + 进程缓存 + 敏感域 DPAPI 加密）。详见 [接口文档 - 配置门面](docs/API.md#配置门面-config原-settingsjson2026-09-06-拆分)。

运维管理面板相关的 API 配置存于 `config/credentials.json` 的 `api_credentials` 键（DPAPI 加密）：

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

也可直接在「设置 → 数据库」页（数据源/双接口账号）中修改并测试连接。

低性能模式（设置 → 性能，键位于 `config/perf.json`）：

```json
"perf_acrylic": false,
"perf_animation": false
```

- `perf_acrylic`：亚克力磨砂效果开关
- `perf_animation`：界面动画开关

均为运行时即时生效，无需重启，低配机器可全部关闭提升流畅度。

上传与 NewLog 批量整理配置（设置 → 面板设置「运维」组，键位于 `config/credentials.json`，密码 DPAPI 加密）：

```json
"upload_host": "你的上传服务器地址",
"upload_port": 22,
"upload_remote_dir": "/your/remote/dir",
"upload_user": "SFTP 账号",
"upload_pass": "...",
"newlog_excel_dir": "C:/Users/xxx/Desktop/excel",
"newlog_out_dir": "C:/Users/xxx/Desktop"
```

- `upload_*`：视频/日志批量整理后的打包上传目标（独立 SFTP 账号，不复用 SSH 凭据，密码 DPAPI 加密）
- `newlog_excel_dir` / `newlog_out_dir`：NewLog 批量整理的署名 Excel 目录与输出目录

AI 分析配置（设置 → 工具「AI 分析」组）：

```json
"ai_vendor": "deepseek",
"ai_model": "",
"forensic_ai_analysis": true,
"ai_api_keys": { "deepseek": "...", "qwen": "..." }
```

- `ai_vendor`：厂商标识，支持 deepseek / qwen / kimi / zhipu / openai / gemini 六家（均走 OpenAI 兼容接口）
- `ai_model`：模型名，留空使用所选厂商默认模型
- `forensic_ai_analysis`：SSH 故障取证报告的 AI 分析开关
- `ai_api_keys`：各厂商 API Key 字典（DPAPI 加密），未配置时回退官方环境变量

数据库设置（售后面板/跑视频面板/运维面板 → 数据库设置，MySQL 主库 + SQLite 兜底）：

```json
"mysql_sync": {
  "enabled": true,
  "host": "你的 MySQL 服务器地址",
  "port": 3306,
  "user": "数据库账号",
  "password": "...",
  "database": "数据库名"
}
```

- `enabled`：开启后 MySQL 完全替代本地 SQLite，应用直接读写远程数据库；关闭回到本地 SQLite
- `password`：DPAPI 加密落盘
- MySQL 不可用时自动降级本地 SQLite（`mark_degraded`），恢复后自动切回并合并兜底增量（`merge_back`）
- 旧字段 `auto_sync` 为镜像推送机制残留，已不再读取

售后统计周期（售后面板 → 设置 → 统计周期设置）：

```json
"aftersale_cycle": { "type": "tue", "start": "2026-08-18", "span": 7 }
```

- `type`：`tue`（周二起，默认）/ `mon`（自然周）/ `custom`（自定义起始日+天数）
- 记录按发生时间动态归属周期，切换模式后列表/统计/周期下拉/导出立即按新规则重新归属

数据保留自动清理（后台自动执行，参数可在 `data_retention` 节点调整）：

```json
"data_retention": {
  "enabled": true,
  "age_days": 60,
  "check_interval_days": 60,
  "max_size_gb": 3,
  "min_size_gb": 2,
  "min_keep_days": 30
}
```

- `enabled`：总开关（关闭后不执行任何清理）
- `age_days`：`xqzg_status` / `kd_status` 过期分区保留天数（默认 60）
- `check_interval_days`：其余流水表的大小检查间隔天数（默认 60）
- `max_size_gb`：触发按大小清理的表大小阈值（默认 3GB）
- `min_size_gb`：清理目标——删到低于该值停止（默认 2GB）
- `min_keep_days`：最低保留天数——最近 N 天数据永不删（默认 30，防误删光）

frp 服务器配置（设置 → 远程连接）：

```json
"frpc_server": {
  "serverAddr": "...",
  "serverPort": .....,
  "auth_method": ".....",
  "auth_token": "..."
}
```

日志高亮规则（设置 → 应用配置「日志高亮」组，默认「错误」红色通知 / 「警告」橙色静默，含旧版「返回」「加分」「add」关键词橙色规则）：

```json
"log_highlight_rules": [
  { "name": "错误", "pattern": "错误|ERROR", "color": [255, 82, 82], "notify": true }
]
```

- 每条规则包含名称、正则 pattern、颜色、通知开关；命中 `notify: true` 规则时弹窗提醒

## 接口文档

完整的模块接口说明（桌面端各层公开接口 + 售后面板 Web API + 配置键路由）请参阅 [docs/API.md](docs/API.md)。

## 开发约定

- **测试**：`pytest tests/ -q`（基线 270 passed）；GUI 冒烟 `python windows/tools/smoke_fluent_mainwindow.py`（offscreen，118 断言）；真机截图 `tools/shot_fluent_mainwindow.py`
- **Web 端验证**：`web/aftersale_front/tools/verify_*.mjs` 系列脚本（产物预检 `verify_prod_layout.mjs`、本地仿真 `serve_dist_v2.mjs`、线上巡检 `verify_prod_deployed.mjs`）
- **部署脚本密码**：一律从环境变量 `AFT_SSH_PASS` 读取，不落盘、不写命令行历史可见位置

## 注意事项

- `autowork_with_table.py` 由 `.ui` 文件编译生成，修改界面请编辑 `.ui` 后重新编译
- P2P 功能需要 `frpc.exe` 与主程序在同一目录下
- 完整版与单文件售后面板**相互独立**：完整版打开售后面板为内置窗口，不调用外部 exe；两者的售后数据分别存于各自 `database/tables.db`，互不影响
- 打包排除了 numpy/scipy（避免 MKL DLL 245MB），亚克力效果由 PIL 补丁替代实现
- 单杆视频渲染**禁止调用 cv2 HighGUI**（`destroyAllWindows`/`imshow` 等）：运行环境的 opencv 无 GUI 后端，调用抛异常导致「视频已写盘却报失败」
- 主题样式文件位于 `styles/`，打包时通过 `AutoWork.spec` 的 `datas` 包含；FluentWindow 禁止窗口级 `setStyleSheet`（破坏 Mica）
- 新增模块请遵循单向依赖链，避免循环导入
- 连接日志自动落盘到 `logs/autowork_conn.log`（2MB 轮转）
- 版本号由 `core/version.py` 自动计算（`BASE_VERSION.git提交数`，当前 BASE=3.11），无 git 环境时回退 `3.11.0`
- 敏感配置（`ssh_pass` / `upload_pass` / `ai_api_keys` 等）经 DPAPI 加密后落盘，换机器或系统用户后需重新填写
- 数据库表结构变更只改 `database/schema.py`（唯一 DDL 来源）：SQLite 自动迁移补列，MySQL 需手动执行生成的 `ALTER TABLE`

## 许可证

石睿轩创作，由沈喆修改第二版。
