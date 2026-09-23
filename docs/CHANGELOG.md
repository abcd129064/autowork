# CHANGELOG — 版本更新记录

> 本文件按**阶段**记录项目演进，条目来源为 git 提交历史（314 次提交，
> 2026-08-04 ~ 2026-09-23）与已落地的设计文档，不逐条罗列提交。
> 版本号规则见 `core/version.py`：`{BASE_VERSION}.{git 提交数}`，
> 当前 BASE_VERSION = **3.13**。
> 作业规范见根目录 [AGENTS.md](../AGENTS.md)，接口契约见 [API.md](API.md)。

状态图例：**已发布** = 已进打包分发 ｜ **已落地** = 已进代码未单独发版 ｜
**进行中** = 当前正在推进

---

## 3.13（2026-09-19 ~ 2026-09-23，进行中）— 远程二期 + frps 感知 + 仓库治理

### 远程 / frp
- 远程页 RemoteHub 由三视图扩为**四视图**：会话总览 │ P2P 访客 │ **连接质量**（新增）│ 隧道配置（`main_window/remote_hub.py`，2026-09-21）。
- frps 在线感知：进入远程页即探测 frps 管理 API，会话总览新增 **frps 概览卡**（`core/frps_admin.py`）；连接质量视图每 30s 一轮探测 visitor 打洞质量。
- frpc visitor **热重载**（`core/frp_remote.py`，2026-09-21）：注册/删除隧道不再整进程重启 frpc。
- **frp 总开关**（2026-09-22）：配置页一键启停全部隧道；首页日志工作台支持排除 frp 日志。
- **隧道断开保留注册**（2026-09-22）：断开 = 置 disabled + apply（落 sidecar `frpc_xtcp_disabled.json`，绝不进 TOML）；删除 = 注册 + TOML + sidecar 全清。
- 开机静默预连（2026-09-23）：启动 6s 后 `autostart()`（仅启用隧道、异常全吞进日志），就绪轮询后 `prewarm_async()` 串行 connect 预热打洞；预连后点 SSH 秒连。详见 [frp-source-integration.md](frp-source-integration.md) 附录 G。
- **远程可靠性批次**（2026-09-23）：①frpc 意外退出退避自愈重启（5s/30s/2min，健康 ≥60s 清零）；②autostart 瞬态失败有界重试（60s/120s）；③质量探测冷洞首拍 3s 长超时不计失败；④会话打开/预热改 bindPort 就绪轮询（200ms 间隔/8s 上限）替代固定延时；⑤隧道失联联动已开会话面板提示条（`windows/remote_session/tunnel_notice.py`）；⑥预热成功状态列「已预热」（`prewarmedAt` 仅内存）；⑦远程页文件归口 `windows/remote_session/`（remote_hub/remote_mixin 迁入）。
- frp 0.65 管理 API 参考手册落档：[frp-065-api-reference.md](frp-065-api-reference.md)。

### 售后 / 跑视频
- 售后记录**连续录入模式**（2026-09-22）：提交后弹窗保持、表单清空，连续登记不关窗。
- 售后/跑视频记录页自动刷新（开关 + 间隔在统一设置页，2026-09-16）：QTimer + 数据指纹比对，刷新保留滚动位与选中行。

### 球桌 / 运维
- 球桌管理新增 **ToDesk 远程开关列**（2026-09-20）：worker 池按设备编码并发下发开/关，权威显示口径 = `todesk_action`。
- 导航栏新增**更新状态图标**（2026-09-20）：发现新版本才显示，位于「设置」上方。

### 工程 / 仓库治理
- **四目录整合**（2026-09-22）：`tests/` `tools/` `design/` `docs/` 职责边界落档为 [AGENTS.md](../AGENTS.md)（硬约束）；`tools/` 按运行方式拆 `deploy/ smoke/ perf/ probe/ stress_test/ update_sim/ _scratch/`。
- 构建修复：PATH 污染导致的 SSL 库冲突（2026-09-22）；conda 构建安全防护与深色主题下售后状态显示（2026-09-21）。
- 文档树补齐（2026-09-23）：新增本文件与 [TODO.md](TODO.md)、[ARCHITECTURE.md](ARCHITECTURE.md)、[DEVELOPMENT.md](DEVELOPMENT.md)、[DEPLOYMENT.md](DEPLOYMENT.md)、[DESIGN.md](DESIGN.md)。

## 3.12（2026-09-06 ~ 2026-09-19，已落地）— FluentWindow 主窗口重构

- **主窗口重构为 FluentWindow 单窗口**（2026-09-06 起，`main_window/`）：左侧导航 工作台 / 运维管理 / 售后 / 跑视频 / 远程 / 工具，底部 设置 / 关于；原各独立面板窗口降层为「Pivot 二级导航 + 工作区」容器页（`main_window/pivot_page.py`）。
- **统一设置页**（Watt Toolkit 式：左标题 + 右 SegmentedWidget 七分页）：应用配置 │ 远程连接 │ 工具 │ 性能 │ 数据库 │ 面板设置 │ 外观；菜单栏整体移入设置页，各面板内部设置页全部迁出（`main_window/hub_pages.py`、`main_window/setting_cards.py`）。
- 大面板按 `windows/<module>/` 子包拆分 + re-export shim 兼容：`windows/aftersale/`、`windows/run_video/`、`windows/management/`、`windows/remote_session/`。
- 工具页 ToolHub 四工作区（单杆视频 / 端口占用 / 上传清单 / 视频与日志批量整理）；远程页 RemoteHub 上线（设计稿 `design/remote_page_v2.html` → v3 → v3_065）。
- Hub 可弹出为独立窗口（`main_window/hub_popout.py`，左侧子导航形态）。
- 启动闪屏 + **程序内自动更新**上线（方案 A：整包 zip + 外部 bat/vbs updater，`core/updater.py` + `tools/deploy/publish_update.py`），入口 = 关于页 + 启动自动检查。
- 默认启动页面可配置（设置 → 应用配置 → 启动，2026-09-19）。
- vue-pure-admin v2 前端工程整合入库（去嵌套 git 仓库，2026-09-19）。
- 摸鱼中心新增扫雷（含自定义难度 + 自动标雷，2026-09-20/21）。

## 3.11（2026-08-29 ~ 2026-09-07，已发布）— 售后面板 Web 化 + 性能基线

- **售后面板 Web 端上线**（2026-08-29 ~ 09-02）：FastAPI 后端 `web/aftersale_api/app.py` + Vue3/Vite v1 前端；生产部署 `http://49.235.34.253/`（入口选择页 → `/v1/`）；JWT + bcrypt 认证、WRITE 开关受环境变量控制。
- 桌面端内置本地 Web 服务 `core/local_web_server.py`（默认 `http://localhost:8787`，托管 v1 产物 + 反代云端 API）。
- **售后性能专项**（2026-09-06 ~ 09-07）：周期归属物化落库 + 覆盖索引，10 万条记录周期筛选 338.9ms → 0.14ms、内存 -74%；DB worker 查询/写槽分离 + 代数守卫修竞争条件；报告见 [售后面板性能调查报告2026-09-06.md](售后面板性能调查报告2026-09-06.md)。
- 设置页控件内联化重构（2026-09-07）；售后「记住发生日期」、自动刷新首版。
- 配置按域拆分 `config/*.json`（2026-09-06，门面 `core/app_settings.py`，敏感域 DPAPI）。

## 3.10（2026-08-18 ~ 2026-08-28，已发布）— 售后系统 + 双后端架构

- **售后管理系统**（2026-08-19 起）：填写录入 / 记录与统计 / 周期管理 / 批量操作 / 导入导出 xlsx / pygwalker 统计图表。
- **MySQL 主库 + SQLite 兜底双后端**（2026-08-18 ~ 08-23）：不可用自动降级、恢复后 LWW 合并回写（`database/backend.py`、`merge_back.py`、`fallback_backup.py`）；设计见 [MySQL兜底降级设计.md](MySQL兜底降级设计.md)。
- **DDL 单一来源治理**（2026-08-23）：`database/schema.py` 收敛 9 张表列/索引/迁移元数据，双方言 DDL 生成；镜像推送机制下线。
- 数据保留自动清理（2026-08-25，`database/data_retention.py`）：过期分区 + 按大小清理，双后端同一套。
- 售后面板独立打包（AfterSale.spec onefile，2026-08-25）。

## 3.9 及以前（2026-08-04 ~ 2026-08-18，已发布）— 远程会话与运维底座

- 远程会话中心：SSH 终端（PTY + ANSI 彩色）、SFTP 双面板文件管理、RDP 窗口嵌入、XTCP P2P visitor 注册（2026-08-04 ~ 08-18）。
- 运维管理面板：球桌管理（wechat2-billiard 接口）、设备状态（kd/xqzg 双源）、图片预览与分类迁移、设备健康度告警（2026-08-14）。
- 单杆视频生成（日志解析 + PIL 计分水印）、上传清单 SFTP 打包上传、端口占用模拟器（2026-08-04 ~ 08-14）。
- AI 智能分析取证报告（六家 OpenAI 兼容厂商，2026-08-08 ~ 08-13）。
- 主题强调色设置、日志高亮规则引擎、统一 `show_info_bar` 提示（2026-08-13 ~ 08-15）。
- 应用图标 v1（2026-08-04；2026-09-06 换 v4 定稿，见 [DESIGN.md](DESIGN.md)）。

---

## 维护约定

- 每个阶段（BASE_VERSION 提升或大特性集落地）补一节，** newest 在最上**。
- 条目写「做了什么 + 落在哪个文件/文档」，不贴 commit hash；需要溯源用
  `git log --format='%ad %s' --date=short` 自查。
- 发版动作（打更新包）记录在 [DEPLOYMENT.md](DEPLOYMENT.md) §4。
