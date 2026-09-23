# ARCHITECTURE — 项目架构、目录结构与数据组织

> 本文是**架构与数据组织的权威说明**：分层依赖、目录职责、数据库表清单、
> 配置路由、双后端机制。接口级契约（类/函数/信号签名）见 [API.md](API.md)；
> 作业硬约束（禁改清单、命名规则、验证基线）见根目录 [AGENTS.md](../AGENTS.md)。
> 最后核对：2026-09-23。

---

## 1. 分层架构（桌面端）

单向依赖，禁止循环导入：

```
core ← win_api ← workers ← windows ← main_window ← main.py
                    ↑          ↑
                    database ──┘
```

| 层 | 目录 | 职责 | 禁止 |
| --- | --- | --- | --- |
| 基础层 | `core/` | 路径/日志/配置门面/性能开关/设计令牌/共享组件/更新器/frp 管理 | 依赖任何上层；import `tools/` |
| Win32 层 | `win_api/` | ctypes 声明：显示设置、窗口嵌入（RDP）、进程挂起恢复 | 业务逻辑 |
| 数据层 | `database/` | SQLite/MySQL 双后端路由、各业务表数据层、DDL 单一来源、降级与合并 | GUI 依赖 |
| Worker 层 | `workers/` | QThread 后台任务：DB 读写、网络（TCP/SFTP/SSH）、视频生成、收集上传、清理/备份/合并 | 直接操作控件 |
| 面板层 | `windows/` | 独立窗口与业务面板；大面板按 `windows/<module>/` 子包拆分，旧入口保留 re-export shim | 跨面板直接 import 内部模块 |
| 主窗口层 | `main_window/` | FluentWindow 主类（Mixin 组合）、Hub 容器页、统一设置页、工具页、远程页 | — |
| 入口 | `main.py` | 薄启动器（内联引导 + 闪屏 + 主窗口） | 业务代码 |

关键横切机制：

- **配置门面**：`core/app_settings.py` 统一读写 `config/*.json`（键自动路由到域文件 + 进程缓存）；`credentials` / `database` 两域经 `core/secrets.py` DPAPI 加解密。任何模块不得直接 open 配置文件。
- **统一提示**：所有 InfoBar 走 `core.utils.show_info_bar()`（右下角、标题按类型映射、贴底遮挡自动抬升）。
- **性能开关**：`core/perf.py` 集中管理亚克力/动画/表格平滑滚动，运行时即时生效。
- **设计令牌**：`core/design_tokens.py` 是语义色/间距/字号单一来源；窗口级 QSS 只能经 `core/theme_qss.py` 应用（FluentWindow 禁止窗口级 `setStyleSheet`，会破坏 Mica）。

## 2. 目录结构总览

```
autowork/
├── main.py / autowork_with_table.py(.ui)   # 入口；经典布局 UI 由 .ui 编译生成，勿手改
├── core/ win_api/ workers/ database/ windows/ main_window/   # 运行时六层（见上表）
├── config/                  # 分域配置（8 个 json，见 §4）
├── styles/                  # dark.qss / light.qss（随包资源，spec datas 打包）
├── resource/                # 随包资源：比分条模板/字体/头像
├── web/                     # 售后面板 Web 端（后端 + v1 + v2 + 入口页，见 §5）
├── tools/                   # 开发/运维脚本（不参与运行时；子目录判据见 AGENTS.md §1.2）
├── tests/                   # pytest 离线单测（仅 test_*.py）
├── docs/                    # 项目文档（索引见 docs/README.md）
├── design/                  # 只读设计资产（HTML 设计稿/logo/生成器/截图）
├── logs/                    # 运行日志（gitignore）
├── build/ dist/ out/        # 构建产物（gitignore）
├── frpc.exe / frpc_xtcp*.toml / frpc_xtcp_meta.json   # frp 运行时二进制与实时配置（禁改）
└── AGENTS.md / README.md    # 作业规范 / 项目门面
```

`tools/` 子目录按**运行方式 + 副作用范围**划分（不是业务主题）：
`deploy/`（改生产）｜`smoke/`（功能通路，可需真机）｜`perf/`（快不快/长得对不对）｜
`probe/`（只读看外部系统）｜`stress_test/`（压测套件）｜`update_sim/`（更新链路本地仿真）｜
`_scratch/`（gitignore，一切临时产物）。

## 3. 数据组织

### 3.1 数据库表（9 张，DDL 单一来源 `database/schema.py`）

| 表 | 内容 | 备注 |
| --- | --- | --- |
| `billiard_tables` | 球桌库（wechat2-billiard 接口落库） | 含设备编码 `code`（界面默认隐藏列） |
| `sync_meta` | 双后端同步元数据（LWW 水位、降级标记） | `key`/`value` 为 MySQL 保留字，生成 DDL 加反引号 |
| `xqzg_status` / `kd_status` | 设备状态快照，按日期分区（`file_path`=`yyyy/MM/dd`） | 过期分区自动清理（默认 60 天） |
| `submission_log` | 提交流水 | MySQL 侧 AUTO_INCREMENT |
| `device_mapping` | 设备 ↔ 球桌映射 | — |
| `health_alerts` | 健康度异常告警（标记已处理/一键归零） | — |
| `aftersale_records` | 售后记录（主业务表） | 周期归属物化落库 + 覆盖索引；Web 端同库 |
| `ledger_records` | 跑视频记录 | 与售后同一条双后端路由 |

- **表结构变更只改 `database/schema.py`**（`TABLE_COLUMNS` / `TABLE_INDEXES` / `MIGRATIONS`）：SQLite 侧 `_ensure_initialized` 自动补列；MySQL 侧按生成的 `ALTER TABLE` 执行（两侧均自动迁移，历史「MySQL 需手动 ALTER」的说法已过时）。
- 运行期 SQL 方言转换在 `database/backend.py::_convert_sql` 管线（占位符、INSERT OR REPLACE、ON CONFLICT、date() 函数、COLLATE、保留字）。**新增 SQLite 专有语法前必须先扩展转换器**，否则 MySQL 主库模式下原样下发会失败。

### 3.2 双后端机制（MySQL 主 + SQLite 兜底）

开关 = `config/database.json` 的 `mysql_sync.enabled`：

```
enabled=true 且 MySQL 可达   → 读写直接走 MySQL（多人协作实时可见）
MySQL 不可用                 → mark_degraded，透明回退本地 SQLite
MySQL 恢复                   → 自动切回 + merge_back（LWW 合并兜底增量）
周备份                       → fallback_backup（MySQL → SQLite 基线刷新）
```

数据保留清理（`database/data_retention.py`）双后端同一套：过期分区每日删；
四张流水表超 `max_size_gb` 从最早日期逐日删到 `min_size_gb`，最近 `min_keep_days` 天受保护。

### 3.3 文件型数据

| 位置 | 内容 |
| --- | --- |
| `config/*.json` | 分域配置（§4） |
| `database/tables.db` | 本地 SQLite（真实业务数据，禁改） |
| `frpc_xtcp.toml` / `frpc_xtcp_meta.json` / sidecar `frpc_xtcp_disabled.json` | frp 实时配置与禁用清单（断开≠删除的落点） |
| `logs/` | 运行日志 + `autowork_conn.log`（2MB 轮转） |
| `moyu_state.json` | 摸鱼中心成绩本地留存 |

## 4. 配置域路由（`core/app_settings.py`）

| 域文件 | 内容 | 加密 |
| --- | --- | --- |
| `aftersale.json` | 售后周期模式/常用句/署名记忆 | 否 |
| `perf.json` | 亚克力/动画/表格平滑滚动 | 否 |
| `database.json` | MySQL 连接 + `data_retention` | **DPAPI** |
| `credentials.json` | SSH/上传/API/AI 凭据、`api_credentials` | **DPAPI** |
| `ui.json` | 主题/字体/DPI/日志高亮规则 | 否 |
| `paths.json` | 程序目录/视频目录等 | 否 |
| `remote.json` | 远程会话列表/隧道密钥 | 否 |
| `misc.json` | 兜底域：`web_port`/`local_web`/`update_base_url`/`update_auto_check`/快捷键等 | 否 |

旧单文件 `settings.json` 首启自动分拣迁移为 `settings.json.bak`（可回滚）。

## 5. Web 端架构（售后面板）

```
浏览器 ── nginx(49.235.34.253:80) ── /            入口选择页（web/aftersale_chooser）
                                  ├── /v1/        v1 SPA（web/aftersale_front/dist）
                                  ├── /v2/        v2 SPA（web/vue-pure-admin/dist）
                                  └── /api/*      aftersale-web.service（uvicorn, web/aftersale_api/app.py）
桌面端 ── core/local_web_server（localhost:8787）── 托管 v1 产物 + /api/* 反代云端（同一数据源）
```

- 后端 FastAPI + pymysql：只读接口默认开放；写接口受 `WRITE_ENABLED`、认证受 `AUTH_ENABLED` 环境变量控制；编辑带 `updated_at` 乐观锁（409）。**pymysql 连接必须 `autocommit=True`**（否则 INSERT 静默回滚）。
- 数据与桌面端售后页**同库同口径**（MySQL `aftersale_records`，周期/筛选口径一致）。
- v2 策略：vue-pure-admin full 版「先不裁剪 demo，只加售后页」；构建基路径 `VITE_PUBLIC_PATH`。

## 6. 远程 / frp 架构

- `core/frp_remote.py`：RemoteSessionManager 全局单例（frpc 进程控制、visitor 注册、会话生命周期），球桌面板/主窗口远程页/会话窗口共享。
- `core/frps_admin.py`：frps 管理 API 客户端（在线感知、概览、连接质量探测），口径锁定 frp 0.65（见 [frp-065-api-reference.md](frp-065-api-reference.md)）。
- 断开 = 置 disabled + apply（TOML 跳过 disabled 项，落 sidecar json）；删除 = 全清。开机静默预连 + 预热打洞见 [frp-source-integration.md](frp-source-integration.md) 附录 G。
- 会话窗口族在 `windows/remote_session/`：SSH（PTY+ANSI）/ SFTP 双面板 / RDP 嵌入 / 取证报告 / 连接诊断。

## 7. 打包与分发

- `build_exe.py` 驱动 PyInstaller 双产物：**完整版 onedir**（`AutoWork.spec`，内置全部面板）与**单文件售后面板 onefile**（`AfterSale.spec`），两者数据相互独立。
- 打包排除 numpy/scipy（避免 MKL DLL 245MB），亚克力由 `core/acrylic_patch.py` PIL 补丁替代。
- 程序内自动更新链路见 [DEPLOYMENT.md](DEPLOYMENT.md) §4 与 [auto_update_research.md](auto_update_research.md)。
